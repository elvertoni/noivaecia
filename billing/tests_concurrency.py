"""PostgreSQL regression tests for billing transaction serialization."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.db.models import Sum
from django.test import TransactionTestCase
from django.utils import timezone

from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from billing.services import (
    PaymentPlanError,
    register_payment,
    register_payment_with_carryover,
    reprocess_future_installments,
)
from customers.models import Customer
from rentals.models import Rental


@skipUnless(connection.vendor == 'postgresql', 'Requer PostgreSQL e SELECT FOR UPDATE.')
class BillingConcurrencyTests(TransactionTestCase):
    """Exercise lock ordering with independent database connections."""

    reset_sequences = True

    def setUp(self):
        CashAccount.objects.create(name='Caixa concorrência')
        self.customer = Customer.objects.create(name='Cliente Concorrência')

    @staticmethod
    def _run_concurrently(*workers):
        barrier = Barrier(len(workers))

        def run(worker):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return worker()
            except Exception as exc:  # Returned so the main test can inspect it.
                return ('error', type(exc), str(exc))
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            futures = [executor.submit(run, worker) for worker in workers]
            return [future.result(timeout=15) for future in futures]

    def test_concurrent_payments_cannot_overpay_the_same_receivable(self):
        rental = Rental.objects.create(
            number=91001,
            customer=self.customer,
            pickup_date=date(2027, 12, 10),
            return_date=date(2027, 12, 15),
            total_value=Decimal('100.00'),
        )
        receivable = Receivable.objects.create(
            rental=rental,
            due_date=date(2027, 11, 10),
            amount=Decimal('100.00'),
        )

        def pay():
            fresh_receivable = Receivable.objects.get(pk=receivable.pk)
            payment = register_payment(
                fresh_receivable,
                Decimal('60.00'),
                timezone.localdate(),
            )
            return ('ok', payment.pk)

        results = self._run_concurrently(pay, pay)

        successful = [result for result in results if result[0] == 'ok']
        rejected = [result for result in results if result[0] == 'error']
        self.assertEqual(len(successful), 1, results)
        self.assertEqual(len(rejected), 1, results)
        self.assertIn('não pode superar o saldo do título', rejected[0][2])

        receivable.refresh_from_db()
        self.assertEqual(receivable.paid_amount, Decimal('60.00'))
        self.assertEqual(receivable.balance, Decimal('40.00'))
        self.assertFalse(Receivable.objects.filter(balance__lt=0).exists())
        self.assertEqual(
            Payment.objects.aggregate(total=Sum('amount'))['total'],
            Decimal('60.00'),
        )
        self.assertEqual(
            FinancialMovement.objects.aggregate(total=Sum('amount'))['total'],
            Decimal('60.00'),
        )

    def test_carryover_and_reprocess_preserve_the_rental_principal(self):
        rental = Rental.objects.create(
            number=91002,
            customer=self.customer,
            pickup_date=date(2027, 12, 10),
            return_date=date(2027, 12, 15),
            total_value=Decimal('180.00'),
        )
        schedule = [
            Receivable.objects.create(
                rental=rental,
                due_date=date(2027, month, 10),
                amount=Decimal('60.00'),
            )
            for month in (9, 10, 11)
        ]
        target_id = schedule[0].pk

        def carryover():
            target = Receivable.objects.get(pk=target_id)
            payments = register_payment_with_carryover(
                target,
                Decimal('90.00'),
                timezone.localdate(),
            )
            return ('ok', 'carryover', len(payments))

        def reprocess():
            fresh_rental = Rental.objects.get(pk=rental.pk)
            created = reprocess_future_installments(
                fresh_rental,
                installments=2,
                first_due_date=date(2027, 10, 10),
            )
            return ('ok', 'reprocess', len(created))

        results = self._run_concurrently(carryover, reprocess)

        successful = [result for result in results if result[0] == 'ok']
        rejected = [result for result in results if result[0] == 'error']
        self.assertEqual(len(successful), 1, results)
        self.assertEqual(len(rejected), 1, results)
        if successful[0][1] == 'carryover':
            self.assertIs(rejected[0][1], PaymentPlanError)
            self.assertIn('parcialmente paga', rejected[0][2])
        else:
            self.assertIn(rejected[0][1], (Receivable.DoesNotExist, ValueError))
            if rejected[0][1] is ValueError:
                self.assertIn('não pertence mais a esta locação', rejected[0][2])

        current_schedule = list(Receivable.objects.filter(rental=rental))
        scheduled_total = sum(
            (receivable.amount for receivable in current_schedule),
            Decimal('0'),
        )
        settled_total = sum(
            (receivable.paid_amount for receivable in current_schedule),
            Decimal('0'),
        )
        open_total = sum(
            (receivable.balance for receivable in current_schedule),
            Decimal('0'),
        )
        payment_total = (
            Payment.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        )
        movement_total = (
            FinancialMovement.objects.aggregate(total=Sum('amount'))['total']
            or Decimal('0')
        )

        self.assertEqual(scheduled_total, Decimal('180.00'))
        self.assertEqual(settled_total + open_total, scheduled_total)
        self.assertEqual(settled_total, payment_total)
        self.assertEqual(movement_total, payment_total)
        self.assertIn(payment_total, (Decimal('0'), Decimal('90.00')))
        self.assertFalse(Receivable.objects.filter(rental=rental, balance__lt=0).exists())
