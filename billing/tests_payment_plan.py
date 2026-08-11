from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ModulePermission
from billing.models import (
    CashAccount,
    FinancialMovement,
    Payment,
    Receipt,
    ReceiptAllocation,
    Receivable,
)
from billing.services import (
    PaymentPlanError,
    create_rental_payment_plan,
    register_payment,
    reprocess_future_installments,
)
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()


class RentalPaymentPlanServiceTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria Silva')
        self.rental = Rental.objects.create(
            number=101,
            customer=self.customer,
            pickup_date=date(2027, 1, 15),
            return_date=date(2027, 1, 20),
            total_value=Decimal('300.00'),
        )
        CashAccount.objects.create(name='Caixa principal')

    def test_entry_and_future_installment_are_distinct_receivables(self):
        result = create_rental_payment_plan(
            self.rental,
            installments=1,
            first_due_date=date(2027, 1, 15),
            down_payment_amount=Decimal('150.00'),
            down_payment_date=date(2026, 7, 27),
            down_payment_method=Payment.Method.PIX,
        )

        receivables = list(self.rental.receivables.order_by('due_date', 'pk'))
        self.assertEqual(len(receivables), 2)
        self.assertEqual(
            [(item.amount, item.paid_amount, item.balance) for item in receivables],
            [
                (Decimal('150.00'), Decimal('150.00'), Decimal('0.00')),
                (Decimal('150.00'), Decimal('0.00'), Decimal('150.00')),
            ],
        )
        self.assertEqual(result['entry_receivable'].pk, receivables[0].pk)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(ReceiptAllocation.objects.count(), 1)
        self.assertEqual(FinancialMovement.objects.count(), 1)
        receipt = Receipt.objects.get()
        allocation = ReceiptAllocation.objects.get(receipt=receipt)
        self.assertEqual(receipt.amount, Decimal('150.00'))
        self.assertEqual(
            receipt.financial_movement_id,
            FinancialMovement.objects.get().pk,
        )
        self.assertEqual(allocation.payment_id, result['entry_payment'].pk)
        self.assertEqual(result['entry_receipt'].pk, receipt.pk)
        self.assertEqual(
            sum((item.amount for item in receivables), Decimal('0')),
            self.rental.total_value,
        )
        self.assertEqual(
            sum((item.balance for item in receivables), Decimal('0')),
            Decimal('150.00'),
        )

    def test_cash_discount_reduces_generated_receivables_total(self):
        self.rental.cash_discount = True
        self.rental.save(update_fields=['cash_discount', 'updated_at'])

        create_rental_payment_plan(
            self.rental,
            installments=1,
            first_due_date=date(2027, 1, 15),
            down_payment_amount=Decimal('30.00'),
            down_payment_date=date(2026, 7, 27),
            down_payment_method=Payment.Method.PIX,
        )

        receivables = list(self.rental.receivables.order_by('due_date', 'pk'))
        self.assertEqual(
            sum((item.amount for item in receivables), Decimal('0')),
            Decimal('270.00'),
        )
        self.assertEqual(self.rental.total_value, Decimal('300.00'))

    def test_full_entry_creates_only_one_paid_receivable(self):
        create_rental_payment_plan(
            self.rental,
            installments=0,
            down_payment_amount=Decimal('300.00'),
            down_payment_date=date(2026, 7, 27),
            down_payment_method=Payment.Method.CASH,
        )

        receivable = self.rental.receivables.get()
        self.assertEqual(receivable.amount, Decimal('300.00'))
        self.assertEqual(receivable.paid_amount, Decimal('300.00'))
        self.assertEqual(receivable.balance, Decimal('0.00'))
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(ReceiptAllocation.objects.count(), 1)

    def test_enforced_plan_requires_a_positive_entry(self):
        with self.assertRaisesMessage(
            PaymentPlanError,
            'Informe uma entrada maior que zero para confirmar a locação.',
        ):
            create_rental_payment_plan(self.rental, installments=1)

        self.assertFalse(Receivable.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_remaining_balance_is_split_across_multiple_future_dates(self):
        create_rental_payment_plan(
            self.rental,
            installments=3,
            down_payment_amount=Decimal('100.00'),
            down_payment_date=date(2026, 7, 27),
            down_payment_method=Payment.Method.CARD_DEBIT,
        )

        future = list(self.rental.receivables.filter(balance__gt=0).order_by('due_date'))
        self.assertEqual(
            [item.due_date for item in future],
            [date(2026, 11, 15), date(2026, 12, 15), date(2027, 1, 15)],
        )
        self.assertEqual(
            sum((item.amount for item in future), Decimal('0')),
            Decimal('200.00'),
        )
        self.assertEqual(len(future), 3)

    def test_entry_above_rental_total_is_rejected_without_partial_writes(self):
        with self.assertRaisesMessage(
            PaymentPlanError,
            'O valor da entrada não pode superar o total da locação.',
        ):
            create_rental_payment_plan(
                self.rental,
                installments=0,
                down_payment_amount=Decimal('300.01'),
                down_payment_date=date(2026, 7, 27),
                down_payment_method=Payment.Method.PIX,
            )

        self.assertFalse(Receivable.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_future_entry_date_is_rejected(self):
        with self.assertRaisesMessage(
            PaymentPlanError,
            'A data da entrada não pode estar no futuro.',
        ):
            create_rental_payment_plan(
                self.rental,
                installments=1,
                first_due_date=date(2027, 1, 15),
                down_payment_amount=Decimal('150.00'),
                down_payment_date=timezone.localdate() + timedelta(days=1),
                down_payment_method=Payment.Method.PIX,
            )

        self.assertFalse(Receivable.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_entry_requires_an_active_cash_account(self):
        CashAccount.objects.all().delete()

        with self.assertRaisesMessage(
            PaymentPlanError,
            'Ative uma conta de caixa antes de registrar a entrada.',
        ):
            create_rental_payment_plan(
                self.rental,
                installments=1,
                first_due_date=date(2027, 1, 15),
                down_payment_amount=Decimal('150.00'),
                down_payment_date=date(2026, 7, 27),
                down_payment_method=Payment.Method.PIX,
            )

        self.assertFalse(Receivable.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_future_due_date_must_follow_entry_date(self):
        with self.assertRaisesMessage(
            PaymentPlanError,
            'As parcelas futuras devem vencer depois da data da entrada.',
        ):
            create_rental_payment_plan(
                self.rental,
                installments=1,
                first_due_date=date(2026, 7, 27),
                down_payment_amount=Decimal('150.00'),
                down_payment_date=date(2026, 7, 27),
                down_payment_method=Payment.Method.PIX,
            )

        self.assertFalse(Receivable.objects.exists())

    def test_last_future_installment_cannot_pass_pickup_date(self):
        with self.assertRaisesMessage(
            PaymentPlanError,
            'A última parcela deve vencer até a data de retirada.',
        ):
            create_rental_payment_plan(
                self.rental,
                installments=3,
                first_due_date=date(2027, 1, 15),
                down_payment_amount=Decimal('100.00'),
                down_payment_date=date(2026, 7, 27),
                down_payment_method=Payment.Method.PIX,
            )

        self.assertFalse(Receivable.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_duplicate_processing_is_rejected_without_duplication(self):
        plan = {
            'installments': 1,
            'first_due_date': date(2027, 1, 15),
            'down_payment_amount': Decimal('150.00'),
            'down_payment_date': date(2026, 7, 27),
            'down_payment_method': Payment.Method.PIX,
        }
        create_rental_payment_plan(self.rental, **plan)

        with self.assertRaisesMessage(
            PaymentPlanError,
            'As condições de pagamento desta locação já foram geradas.',
        ):
            create_rental_payment_plan(self.rental, **plan)

        self.assertEqual(Receivable.objects.count(), 2)
        self.assertEqual(Payment.objects.count(), 1)

    def test_plan_is_atomic_when_future_installment_generation_fails(self):
        with mock.patch(
            'billing.services.generate_for_rental',
            side_effect=RuntimeError('generation failed'),
        ):
            with self.assertRaises(RuntimeError):
                create_rental_payment_plan(
                    self.rental,
                    installments=1,
                    first_due_date=date(2027, 1, 15),
                    down_payment_amount=Decimal('150.00'),
                    down_payment_date=date(2026, 7, 27),
                    down_payment_method=Payment.Method.PIX,
                )

        self.assertFalse(Receivable.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(FinancialMovement.objects.exists())

    def test_reprocessing_preserves_entry_and_replaces_only_future_schedule(self):
        plan = create_rental_payment_plan(
            self.rental,
            installments=1,
            first_due_date=date(2027, 1, 15),
            down_payment_amount=Decimal('150.00'),
            down_payment_date=date(2026, 7, 27),
            down_payment_method=Payment.Method.PIX,
        )
        entry_pk = plan['entry_receivable'].pk
        previous_future_pk = plan['future_receivables'][0].pk

        result = reprocess_future_installments(
            self.rental,
            installments=3,
        )

        self.assertEqual([item.pk for item in result['protected']], [entry_pk])
        self.assertTrue(Receivable.objects.filter(pk=entry_pk).exists())
        self.assertFalse(Receivable.objects.filter(pk=previous_future_pk).exists())
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(self.rental.receivables.count(), 4)
        self.assertEqual(
            sum(
                (item.amount for item in self.rental.receivables.all()),
                Decimal('0'),
            ),
            Decimal('300.00'),
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action='reprocess_future_installments',
                object_id=str(self.rental.pk),
            ).exists()
        )

    def test_reprocessing_splits_legacy_partial_title_into_paid_and_future_parts(self):
        self.rental.financial_policy_version = Rental.FinancialPolicy.LEGACY_ACCESS
        self.rental.save(update_fields=['financial_policy_version', 'updated_at'])
        legacy_receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=date(2027, 1, 15),
            amount=Decimal('300.00'),
        )
        register_payment(
            legacy_receivable,
            amount=Decimal('150.00'),
            payment_date=date(2026, 7, 27),
            method=Payment.Method.PIX,
        )

        result = reprocess_future_installments(
            self.rental,
            installments=1,
            first_due_date=date(2027, 1, 15),
        )

        legacy_receivable.refresh_from_db()
        self.assertEqual(legacy_receivable.amount, Decimal('150.00'))
        self.assertEqual(legacy_receivable.paid_amount, Decimal('150.00'))
        self.assertEqual(legacy_receivable.balance, Decimal('0.00'))
        self.assertEqual(result['created'][0].amount, Decimal('150.00'))
        self.assertEqual(result['created'][0].balance, Decimal('150.00'))
        self.assertEqual(
            sum(
                (item.amount for item in self.rental.receivables.all()),
                Decimal('0'),
            ),
            self.rental.total_value,
        )
        audit = AuditLog.objects.get(action='reprocess_future_installments')
        self.assertEqual(
            audit.metadata['adjusted_partial_receivable_ids'],
            [legacy_receivable.pk],
        )

    def test_reprocessing_enforced_partial_title_freezes_paid_part_and_discloses_it(self):
        """ENFORCED_V1 used to refuse this outright, freezing the schedule."""
        receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=self.rental.pickup_date,
            amount=Decimal('200.00'),
        )
        payment = register_payment(
            receivable,
            amount=Decimal('100.00'),
            payment_date=date(2026, 7, 27),
            method=Payment.Method.PIX,
        )

        result = reprocess_future_installments(
            self.rental,
            installments=1,
            first_due_date=date(2027, 1, 15),
        )

        # The paid part is frozen as a closed historical title; its open
        # balance goes back into the pool that feeds the new schedule.
        receivable.refresh_from_db()
        self.assertEqual(receivable.amount, Decimal('100.00'))
        self.assertEqual(receivable.paid_amount, Decimal('100.00'))
        self.assertEqual(receivable.balance, Decimal('0.00'))
        self.assertEqual(result['created'][0].amount, Decimal('200.00'))
        self.assertEqual(result['released_amount'], Decimal('100.00'))
        self.assertEqual(
            result['adjusted_partials'],
            [{
                'id': receivable.pk,
                'due_date': self.rental.pickup_date.isoformat(),
                'previous_amount': '200.00',
                'amount': '100.00',
                'released_amount': '100.00',
            }],
        )

        # The rewrite is disclosed, never silent.
        audit = AuditLog.objects.get(action='reprocess_future_installments')
        self.assertEqual(
            audit.metadata['adjusted_partial_receivable_ids'], [receivable.pk],
        )
        self.assertEqual(audit.metadata['released_amount'], '100.00')

        # Invariants: the schedule still sums to the rental value, and the
        # payment is untouched and still attached to its original title.
        self.assertEqual(
            sum(
                (item.amount for item in self.rental.receivables.all()),
                Decimal('0'),
            ),
            self.rental.total_value,
        )
        payment.refresh_from_db()
        self.assertEqual(payment.receivable_id, receivable.pk)
        self.assertEqual(Payment.objects.count(), 1)

    def test_reprocessing_rejects_due_date_before_preserved_entry(self):
        plan = create_rental_payment_plan(
            self.rental,
            installments=1,
            first_due_date=date(2027, 1, 15),
            down_payment_amount=Decimal('150.00'),
            down_payment_date=date(2026, 7, 27),
            down_payment_method=Payment.Method.PIX,
        )
        future_pk = plan['future_receivables'][0].pk

        with self.assertRaisesMessage(
            PaymentPlanError,
            'O primeiro vencimento futuro deve ser posterior ao último recebimento.',
        ):
            reprocess_future_installments(
                self.rental,
                installments=2,
                first_due_date=date(2026, 7, 27),
            )

        self.assertTrue(Receivable.objects.filter(pk=future_pk).exists())
        self.assertEqual(self.rental.receivables.count(), 2)


class RentalContractPaymentPlanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='contrato@example.com',
            password='Senha12345',
        )
        ModulePermission.objects.create(
            user=self.user,
            module_key='rentals',
            allowed=True,
        )
        self.client.force_login(self.user)
        CashAccount.objects.create(name='Caixa principal')
        customer = Customer.objects.create(
            name='Maria Silva',
            phone_home='43 99999-0000',
            alternate_phone_contact='Diogo',
        )
        self.rental = Rental.objects.create(
            number=102,
            customer=customer,
            pickup_date=date(2027, 1, 15),
            return_date=date(2027, 1, 20),
            total_value=Decimal('300.00'),
        )
        create_rental_payment_plan(
            self.rental,
            installments=1,
            first_due_date=date(2027, 1, 15),
            down_payment_amount=Decimal('150.00'),
            down_payment_date=date(2026, 7, 27),
            down_payment_method=Payment.Method.PIX,
        )

    def test_contract_uses_clear_pickup_return_and_payment_conditions(self):
        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Retirada dos trajes')
        self.assertContains(response, 'Devolução dos trajes')
        self.assertContains(response, '27/07/2026')
        self.assertContains(response, '15/01/2027')
        self.assertContains(response, 'Recebido')
        self.assertContains(response, 'Saldo em aberto')
        self.assertContains(response, 'Telefone alternativo')
        self.assertContains(response, 'Diogo / 43 99999-0000')
        self.assertContains(response, 'Locatário(a): MARIA SILVA')
        self.rental.refresh_from_db()
        self.assertEqual(self.rental.contract_version, 'v3')

    def test_contract_payment_matrix_shows_all_ten_installments(self):
        customer = Customer.objects.create(name='Joana Souza')
        rental = Rental.objects.create(
            number=103,
            customer=customer,
            pickup_date=date(2027, 4, 20),
            return_date=date(2027, 4, 25),
            total_value=Decimal('1000.00'),
        )
        create_rental_payment_plan(
            rental,
            installments=9,
            down_payment_amount=Decimal('100.00'),
            down_payment_date=date(2026, 7, 20),
            down_payment_method=Payment.Method.PIX,
        )
        receivables = list(rental.receivables.order_by('due_date', 'pk'))
        self.assertEqual(len(receivables), 10)

        response = self.client.get(reverse('rentals:contract', args=[rental.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<th style="width:8%">10ª</th>', html=False)
        last_installment = receivables[-1]
        self.assertContains(response, f'R$ {last_installment.amount:.2f}'.replace('.', ','))

    def test_contract_distinguishes_partial_receipt_and_write_off(self):
        partial = Receivable.objects.create(
            rental=self.rental,
            due_date=date(2027, 2, 15),
            amount=Decimal('100.00'),
            paid_amount=Decimal('50.00'),
            last_payment_date=date(2026, 8, 1),
        )

        partial_response = self.client.get(
            reverse('rentals:contract', args=[self.rental.pk])
        )
        self.assertContains(partial_response, 'Recebida parcialmente em 01/08/2026')

        partial.written_off_at = timezone.now()
        partial.written_off_reason = 'Baixa aprovada'
        partial.save()
        written_off_response = self.client.get(
            reverse('rentals:contract', args=[self.rental.pk])
        )
        self.assertContains(written_off_response, 'Baixada')
