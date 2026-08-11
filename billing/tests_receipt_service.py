"""Atomic real-receipt registration and reversal services."""

import uuid
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from customers.models import Customer
from rentals.models import Rental

from .models import (
    CashAccount,
    FinancialMovement,
    Payment,
    Receivable,
    Receipt,
    ReceiptAllocation,
)
from .services import (
    ReceiptIdempotencyConflict,
    ReceiptServiceError,
    reconcile_financial,
    register_receipt,
    reverse_receipt,
)


class ReceiptServiceTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.user = get_user_model().objects.create_user(
            email='receipt-service@test.com',
            password='pass',
        )
        self.customer = Customer.objects.create(name='Cliente Serviço Recibo')
        self.rental = Rental.objects.create(
            number=99001,
            customer=self.customer,
            pickup_date=self.today,
            return_date=self.today,
            total_value=Decimal('200.00'),
        )
        self.account = CashAccount.objects.create(name='Caixa serviço')
        self.first = Receivable.objects.create(
            rental=self.rental,
            due_date=self.today,
            amount=Decimal('100.00'),
        )
        self.second = Receivable.objects.create(
            rental=self.rental,
            due_date=self.today,
            amount=Decimal('100.00'),
        )
        self.key = uuid.uuid4()

    def _payload(self):
        return {
            'rental_id': self.rental.pk,
            'cash_account_id': self.account.pk,
            'received_on': self.today,
            'amount': Decimal('100.00'),
            'method': Payment.Method.PIX,
            'notes': 'Recebimento no balcão',
            'allocations': [
                {
                    'receivable_id': self.first.pk,
                    'cash_amount': Decimal('70.00'),
                    'interest_amount': Decimal('10.00'),
                    'discount_amount': Decimal('5.00'),
                },
                {
                    'receivable_id': self.second.pk,
                    'cash_amount': Decimal('30.00'),
                },
            ],
        }

    def _register(self, **kwargs):
        values = {
            'idempotency_key': self.key,
            'payload': self._payload(),
            'user': self.user,
        }
        values.update(kwargs)
        return register_receipt(**values)

    def test_registers_one_cash_event_with_multiple_allocations(self):
        receipt = self._register()

        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(ReceiptAllocation.objects.count(), 2)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 1)
        self.assertEqual(receipt.amount, Decimal('100.00'))
        self.assertEqual(receipt.operator, self.user)
        movement = receipt.financial_movement
        self.assertEqual(movement.direction, FinancialMovement.Direction.INFLOW)
        self.assertEqual(movement.amount, receipt.amount)
        self.assertEqual(movement.account, self.account)
        self.assertIsNone(movement.payment)

    def test_payment_and_allocation_principal_match_cash_discount_and_interest(self):
        receipt = self._register()

        allocation = receipt.allocations.get(receivable=self.first)
        self.assertEqual(allocation.cash_amount, Decimal('70.00'))
        self.assertEqual(allocation.principal_amount, Decimal('65.00'))
        self.assertEqual(allocation.interest_amount, Decimal('10.00'))
        self.assertEqual(allocation.discount_amount, Decimal('5.00'))
        self.assertEqual(allocation.payment.amount, Decimal('70.00'))
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.paid_amount, Decimal('65.00'))
        self.assertEqual(self.first.balance, Decimal('35.00'))
        self.assertEqual(self.second.balance, Decimal('70.00'))

    def test_multi_allocation_receipt_is_valid_in_financial_reconciliation(self):
        self._register()

        reconciliation = reconcile_financial()

        self.assertEqual(reconciliation['payments_with_movement_issue_count'], 0)
        self.assertEqual(reconciliation['payments_without_movement_count'], 0)

    def test_same_key_and_payload_returns_existing_receipt(self):
        first_result = self._register()
        second_result = self._register()

        self.assertEqual(second_result.pk, first_result.pk)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(ReceiptAllocation.objects.count(), 2)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 1)

    def test_same_key_with_different_payload_is_rejected(self):
        self._register()
        changed = self._payload()
        changed['notes'] = 'Conteúdo alterado'

        with self.assertRaises(ReceiptIdempotencyConflict):
            self._register(payload=changed)

        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(FinancialMovement.objects.count(), 1)

    def test_duplicate_receivable_in_same_receipt_is_rejected(self):
        payload = self._payload()
        payload['allocations'][1]['receivable_id'] = self.first.pk

        with self.assertRaisesMessage(
            ReceiptServiceError,
            'Cada título pode ser alocado apenas uma vez',
        ):
            self._register(payload=payload)

        self.assertFalse(Receipt.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_receipt_amount_must_equal_allocated_cash(self):
        payload = self._payload()
        payload['amount'] = Decimal('99.99')

        with self.assertRaisesMessage(
            ReceiptServiceError,
            'igual à soma em caixa das alocações',
        ):
            self._register(payload=payload)

        self.assertFalse(Receipt.objects.exists())

    def test_receivable_from_another_rental_is_rejected(self):
        other_rental = Rental.objects.create(
            number=99002,
            customer=self.customer,
            pickup_date=self.today,
            return_date=self.today,
            total_value=Decimal('100.00'),
        )
        other = Receivable.objects.create(
            rental=other_rental,
            due_date=self.today,
            amount=Decimal('100.00'),
        )
        payload = self._payload()
        payload['allocations'][1]['receivable_id'] = other.pk

        with self.assertRaisesMessage(
            ReceiptServiceError,
            'não pertencem à locação',
        ):
            self._register(payload=payload)

        self.assertFalse(Receipt.objects.exists())

    def test_inactive_cash_account_is_rejected(self):
        self.account.active = False
        self.account.save(update_fields=['active', 'updated_at'])

        with self.assertRaisesMessage(ReceiptServiceError, 'está inativa'):
            self._register()

        self.assertFalse(Receipt.objects.exists())

    def test_principal_cannot_exceed_receivable_balance(self):
        payload = self._payload()
        payload['amount'] = Decimal('201.00')
        payload['allocations'][0] = {
            'receivable_id': self.first.pk,
            'cash_amount': Decimal('101.00'),
        }
        payload['allocations'][1]['cash_amount'] = Decimal('100.00')

        with self.assertRaisesMessage(ReceiptServiceError, 'supera seu saldo'):
            self._register(payload=payload)

        self.assertFalse(Receipt.objects.exists())

    def test_failure_creating_movement_rolls_back_every_financial_row(self):
        with mock.patch(
            'billing.services.FinancialMovement.objects.create',
            side_effect=RuntimeError('movement failed'),
        ):
            with self.assertRaises(RuntimeError):
                self._register()

        self.assertFalse(Receipt.objects.exists())
        self.assertFalse(ReceiptAllocation.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.first.refresh_from_db()
        self.assertEqual(self.first.balance, Decimal('100.00'))


class ReceiptReversalServiceTests(TestCase):
    def setUp(self):
        ReceiptServiceTests.setUp(self)
        self.original = register_receipt(
            idempotency_key=self.key,
            payload=ReceiptServiceTests._payload(self),
            user=self.user,
        )
        self.reversal_key = uuid.uuid4()

    def _reverse(self, receipt=None, key=None, payload=None):
        return reverse_receipt(
            receipt or self.original,
            idempotency_key=key or self.reversal_key,
            payload=payload or {
                'received_on': self.today,
                'reason': 'Lançamento incorreto',
            },
            user=self.user,
        )

    def test_reverses_all_allocations_with_one_outflow(self):
        reversal = self._reverse()

        self.assertEqual(reversal.kind, Receipt.Kind.REVERSAL)
        self.assertEqual(reversal.reversal_of, self.original)
        self.assertEqual(reversal.amount, self.original.amount)
        self.assertEqual(reversal.allocations.count(), 2)
        self.assertEqual(Payment.objects.filter(is_reversal=True).count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 2)
        self.assertEqual(
            reversal.financial_movement.direction,
            FinancialMovement.Direction.OUTFLOW,
        )
        self.assertEqual(reversal.financial_movement.account, self.account)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.balance, Decimal('100.00'))
        self.assertEqual(self.second.balance, Decimal('100.00'))

    def test_grouped_reversal_is_valid_in_financial_reconciliation(self):
        self._reverse()

        reconciliation = reconcile_financial()

        self.assertEqual(reconciliation['payments_with_movement_issue_count'], 0)
        self.assertEqual(reconciliation['reversals_with_movement_issue_count'], 0)

    def test_reversal_uses_original_account_even_when_inactive(self):
        self.account.active = False
        self.account.save(update_fields=['active', 'updated_at'])

        reversal = self._reverse()

        self.assertEqual(reversal.financial_movement.account, self.account)

    def test_reversal_retry_is_idempotent(self):
        first_result = self._reverse()
        second_result = self._reverse()

        self.assertEqual(second_result.pk, first_result.pk)
        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 2)

    def test_reversal_key_with_different_payload_is_rejected(self):
        self._reverse()

        with self.assertRaises(ReceiptIdempotencyConflict):
            self._reverse(payload={
                'received_on': self.today,
                'reason': 'Outro motivo',
            })

    def test_second_reversal_with_another_key_is_rejected(self):
        self._reverse()

        with self.assertRaisesMessage(ReceiptServiceError, 'já foi estornado'):
            self._reverse(key=uuid.uuid4())

    def test_reversal_of_reversal_is_rejected(self):
        reversal = self._reverse()

        with self.assertRaisesMessage(
            ReceiptServiceError,
            'Não é possível estornar um estorno',
        ):
            self._reverse(receipt=reversal, key=uuid.uuid4())
