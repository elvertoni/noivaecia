"""The billing screens must write real cash acts, not loose payments.

Each test here pins one property the UI lost while ``Receipt`` was implemented
but unwired: one cash movement per act, reversal that undoes the act as a whole,
and a double submit that costs the customer nothing twice.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ActionPermission, ModulePermission
from company.models import Company
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
from .services import reconcile_financial

User = get_user_model()


class ReceiptViewTestCase(TestCase):
    """One rental with a four-installment schedule and a cashier who can act."""

    def setUp(self):
        self.today = timezone.localdate()
        Company.objects.filter(pk=1).delete()
        Company.objects.create(
            name='Noivas Cia',
            last_rental_number=1,
            daily_interest_rate=Decimal('1.00'),
        )
        self.user = User.objects.create_user(
            email='caixa@noivascia.com.br',
            password='Senha12345',
        )
        ModulePermission.objects.create(
            user=self.user, module_key='billing', allowed=True,
        )
        for action_key in ('billing.receive', 'billing.reverse'):
            ActionPermission.objects.create(
                user=self.user, action_key=action_key, allowed=True,
            )
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(name='Maria Silva', city='Recife')
        self.rental = Rental.objects.create(
            number=500,
            customer=self.customer,
            pickup_date=self.today + timedelta(days=30),
            return_date=self.today + timedelta(days=40),
            total_value=Decimal('240.00'),
        )
        self.account = CashAccount.objects.create(name='Caixa', active=True)
        self.installments = [
            Receivable.objects.create(
                rental=self.rental,
                due_date=self.today + timedelta(days=30 * index),
                amount=Decimal('60.00'),
            )
            for index in range(4)
        ]

    def _pay(self, receivable, value, token=None, follow=False):
        return self.client.post(
            reverse('billing:pay', args=[receivable.pk]),
            {
                'value': value,
                'payment_date': self.today.strftime('%d/%m/%Y'),
                'submission_token': token or str(uuid.uuid4()),
            },
            follow=follow,
        )


class PaymentViewReceiptTests(ReceiptViewTestCase):
    """``billing:pay`` must register the whole cash act as one receipt."""

    def test_one_submission_creates_a_single_receipt_and_one_movement(self):
        response = self._pay(self.installments[0], '200,00', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'distribuído entre 4 parcelas')
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(ReceiptAllocation.objects.count(), 4)
        self.assertEqual(Payment.objects.count(), 4)
        self.assertEqual(FinancialMovement.objects.count(), 1)

        receipt = Receipt.objects.get()
        self.assertEqual(receipt.amount, Decimal('200.00'))
        self.assertEqual(receipt.operator, self.user)
        movement = receipt.financial_movement
        self.assertEqual(movement.direction, FinancialMovement.Direction.INFLOW)
        self.assertEqual(movement.amount, Decimal('200.00'))

    def test_allocation_order_matches_the_legacy_cascade(self):
        self._pay(self.installments[0], '200,00')

        allocations = list(
            ReceiptAllocation.objects.order_by('receivable__due_date')
            .values_list('cash_amount', flat=True)
        )
        self.assertEqual(
            allocations,
            [
                Decimal('60.00'),
                Decimal('60.00'),
                Decimal('60.00'),
                Decimal('20.00'),
            ],
        )
        for installment in self.installments[:3]:
            installment.refresh_from_db()
            self.assertEqual(installment.balance, Decimal('0.00'))
        self.installments[3].refresh_from_db()
        self.assertEqual(self.installments[3].balance, Decimal('40.00'))

    def test_double_submit_with_the_same_token_registers_the_money_once(self):
        token = str(uuid.uuid4())

        self._pay(self.installments[0], '200,00', token=token)
        self._pay(self.installments[0], '200,00', token=token)

        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 4)
        self.assertEqual(FinancialMovement.objects.count(), 1)
        self.assertEqual(
            Receipt.objects.get().amount,
            Decimal('200.00'),
        )

    def test_distinct_submissions_register_distinct_receipts(self):
        self._pay(self.installments[0], '60,00')
        self._pay(self.installments[1], '60,00')

        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 2)

    def test_amount_above_the_rental_capacity_is_refused_on_the_field(self):
        response = self._pay(self.installments[0], '500,00')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'maior que o saldo total desta locação')
        self.assertFalse(Receipt.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(FinancialMovement.objects.exists())

    def test_receipt_written_by_the_view_reconciles_clean(self):
        self._pay(self.installments[0], '200,00')

        reconciliation = reconcile_financial()

        self.assertEqual(reconciliation['payments_with_movement_issue_count'], 0)
        self.assertEqual(reconciliation['reversals_with_movement_issue_count'], 0)


class ReceiptReversalViewTests(ReceiptViewTestCase):
    """Reversing any slice of a receipt must undo the whole cash act."""

    def test_reversing_one_allocation_reverses_the_entire_receipt(self):
        self._pay(self.installments[0], '200,00')
        receipt = Receipt.objects.get()
        first_payment = receipt.allocations.order_by('pk').first().payment

        response = self.client.post(
            reverse('billing:reverse_payment', args=[first_payment.pk]),
            {'reason': 'Cheque devolvido', 'submission_token': str(uuid.uuid4())},
        )

        self.assertEqual(response.status_code, 302)
        receipt.refresh_from_db()
        reversal = receipt.reversal
        self.assertEqual(reversal.kind, Receipt.Kind.REVERSAL)
        self.assertEqual(reversal.amount, receipt.amount)

        # One act out, one act in: exactly two movements in total.
        self.assertEqual(FinancialMovement.objects.count(), 2)
        outflows = FinancialMovement.objects.filter(
            direction=FinancialMovement.Direction.OUTFLOW,
        )
        self.assertEqual(outflows.count(), 1)
        self.assertEqual(outflows.get().amount, Decimal('200.00'))

        # Every title the receipt had settled is open again.
        for installment in self.installments:
            installment.refresh_from_db()
            self.assertEqual(installment.balance, Decimal('60.00'))
            self.assertEqual(installment.paid_amount, Decimal('0.00'))

    def test_single_allocation_receipt_reverses_through_the_receipt_model(self):
        """The D2 corruption: a one-title receipt used to reverse silently wrong.

        ``reverse_payment`` accepted it because the receipt's grouped movement
        happens to be linked to that single payment, which minted a second cash
        outflow while leaving ``Receipt.reversal`` unset.
        """
        self._pay(self.installments[0], '60,00')
        receipt = Receipt.objects.get()
        self.assertEqual(receipt.allocations.count(), 1)
        payment = receipt.allocations.get().payment

        self.client.post(
            reverse('billing:reverse_payment', args=[payment.pk]),
            {'reason': 'Erro de digitação', 'submission_token': str(uuid.uuid4())},
        )

        receipt.refresh_from_db()
        self.assertTrue(hasattr(receipt, 'reversal'))
        self.assertEqual(receipt.reversal.reversal_of_id, receipt.pk)
        self.assertEqual(
            FinancialMovement.objects.filter(
                direction=FinancialMovement.Direction.OUTFLOW,
            ).count(),
            1,
        )
        self.installments[0].refresh_from_db()
        self.assertEqual(self.installments[0].balance, Decimal('60.00'))

    def test_reversal_screen_shows_the_whole_act_not_only_the_slice(self):
        self._pay(self.installments[0], '200,00')
        receipt = Receipt.objects.get()
        first_payment = receipt.allocations.order_by('pk').first().payment

        response = self.client.get(
            reverse('billing:reverse_payment', args=[first_payment.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['receipt'], receipt)
        self.assertEqual(len(response.context['receipt_allocations']), 4)
        self.assertContains(response, 'Valor total do recibo')
        self.assertContains(response, '200,00')

    def test_second_slice_cannot_be_reversed_after_the_receipt_is_undone(self):
        self._pay(self.installments[0], '200,00')
        receipt = Receipt.objects.get()
        payments = [
            allocation.payment
            for allocation in receipt.allocations.order_by('pk')
        ]
        self.client.post(
            reverse('billing:reverse_payment', args=[payments[0].pk]),
            {'reason': 'Cheque devolvido', 'submission_token': str(uuid.uuid4())},
        )

        response = self.client.get(
            reverse('billing:reverse_payment', args=[payments[1].pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            FinancialMovement.objects.filter(
                direction=FinancialMovement.Direction.OUTFLOW,
            ).count(),
            1,
        )

    def test_reversed_receipt_reconciles_clean(self):
        self._pay(self.installments[0], '200,00')
        payment = Receipt.objects.get().allocations.order_by('pk').first().payment
        self.client.post(
            reverse('billing:reverse_payment', args=[payment.pk]),
            {'reason': 'Cheque devolvido', 'submission_token': str(uuid.uuid4())},
        )

        reconciliation = reconcile_financial()

        self.assertEqual(reconciliation['payments_with_movement_issue_count'], 0)
        self.assertEqual(reconciliation['reversals_with_movement_issue_count'], 0)
        self.assertEqual(reconciliation['net_movements'], Decimal('0.00'))


class PaymentCorrectionFlowTests(ReceiptViewTestCase):
    """A typing error can be corrected from the installment that shows it."""

    def setUp(self):
        super().setUp()
        self._pay(self.installments[0], '30,00')
        self.receipt = Receipt.objects.get()
        self.payment = self.receipt.allocations.get().payment

    def test_rental_installment_exposes_correction_next_to_receiving(self):
        response = self.client.get(
            reverse('billing:list', args=[self.rental.pk]),
        )

        self.assertContains(response, 'Receber')
        self.assertContains(response, 'Corrigir recebimento')
        self.assertContains(
            response,
            reverse('billing:reverse_payment', args=[self.payment.pk]),
        )

    def test_correction_action_requires_receive_and_reverse_permissions(self):
        ActionPermission.objects.filter(
            user=self.user,
            action_key='billing.reverse',
        ).delete()

        response = self.client.get(
            reverse('billing:list', args=[self.rental.pk]),
        )

        self.assertNotContains(response, 'Corrigir recebimento')

    def test_correction_url_cannot_bypass_receive_permission(self):
        ActionPermission.objects.filter(
            user=self.user,
            action_key='billing.receive',
        ).delete()

        response = self.client.post(
            reverse('billing:reverse_payment', args=[self.payment.pk]),
            {
                'reason': 'Correção de valor digitado incorretamente.',
                'submission_token': str(uuid.uuid4()),
                'corrigir': '1',
            },
        )

        self.assertEqual(response.status_code, 403)
        self.receipt.refresh_from_db()
        self.assertFalse(hasattr(self.receipt, 'reversal'))

    def test_correction_confirmation_explains_and_prefills_the_reason(self):
        response = self.client.get(
            reverse('billing:reverse_payment', args=[self.payment.pk]),
            {
                'corrigir': '1',
                'next': reverse('billing:list', args=[self.rental.pk]),
            },
        )

        self.assertContains(response, 'Corrigir recebimento')
        self.assertContains(response, 'Estornar e corrigir')
        self.assertContains(response, 'Correção de valor digitado incorretamente.')
        self.assertEqual(
            response.context['return_url'],
            reverse('billing:list', args=[self.rental.pk]),
        )

    def test_correction_confirmation_rejects_external_return_url(self):
        response = self.client.get(
            reverse('billing:reverse_payment', args=[self.payment.pk]),
            {
                'corrigir': '1',
                'next': 'https://example.net/steal-session',
            },
        )

        self.assertIsNone(response.context['return_url'])
        self.assertNotContains(response, 'https://example.net/steal-session')

    def test_confirming_correction_reverses_then_opens_the_same_installment(self):
        response = self.client.post(
            reverse('billing:reverse_payment', args=[self.payment.pk]),
            {
                'reason': 'Correção de valor digitado incorretamente.',
                'submission_token': str(uuid.uuid4()),
                'corrigir': '1',
                'next': reverse('billing:list', args=[self.rental.pk]),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.redirect_chain[-1][0],
            reverse('billing:pay', args=[self.installments[0].pk]),
        )
        self.receipt.refresh_from_db()
        self.installments[0].refresh_from_db()
        self.assertTrue(hasattr(self.receipt, 'reversal'))
        self.assertEqual(self.installments[0].paid_amount, Decimal('0.00'))
        self.assertEqual(self.installments[0].balance, Decimal('60.00'))
        self.assertContains(
            response,
            'Recebimento de R$ 30,00 estornado. Informe abaixo o valor correto',
        )

    def test_correct_value_is_a_new_auditable_receipt(self):
        self.client.post(
            reverse('billing:reverse_payment', args=[self.payment.pk]),
            {
                'reason': 'Correção de valor digitado incorretamente.',
                'submission_token': str(uuid.uuid4()),
                'corrigir': '1',
            },
        )

        self._pay(self.installments[0], '45,00')

        self.receipt.refresh_from_db()
        self.installments[0].refresh_from_db()
        corrected_receipt = (
            Receipt.objects.filter(kind=Receipt.Kind.INFLOW)
            .exclude(pk=self.receipt.pk)
            .get()
        )
        self.assertTrue(hasattr(self.receipt, 'reversal'))
        self.assertEqual(corrected_receipt.amount, Decimal('45.00'))
        self.assertEqual(self.installments[0].paid_amount, Decimal('45.00'))
        self.assertEqual(self.installments[0].balance, Decimal('15.00'))
        self.assertEqual(Receipt.objects.count(), 3)
        self.assertEqual(FinancialMovement.objects.count(), 3)


class ReceivablePayViewReceiptTests(ReceiptViewTestCase):
    """The single-title screen writes a one-allocation receipt."""

    def test_interest_and_discount_ride_inside_one_allocation(self):
        response = self.client.post(
            reverse('billing:pay_receivable', args=[self.installments[0].pk]),
            {
                'amount': '55,00',
                'payment_date': self.today.strftime('%d/%m/%Y'),
                'method': 'pix',
                'interest_amount': '0',
                'discount_amount': '5,00',
                'notes': 'Desconto combinado',
                'submission_token': str(uuid.uuid4()),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(FinancialMovement.objects.count(), 1)

        allocation = ReceiptAllocation.objects.get()
        self.assertEqual(allocation.cash_amount, Decimal('55.00'))
        self.assertEqual(allocation.discount_amount, Decimal('5.00'))
        self.assertEqual(allocation.principal_amount, Decimal('60.00'))
        self.installments[0].refresh_from_db()
        self.assertEqual(self.installments[0].balance, Decimal('0.00'))

    def test_double_submit_with_the_same_token_registers_the_money_once(self):
        token = str(uuid.uuid4())
        payload = {
            'amount': '60,00',
            'payment_date': self.today.strftime('%d/%m/%Y'),
            'method': 'cash',
            'interest_amount': '0',
            'discount_amount': '0',
            'notes': '',
            'submission_token': token,
        }

        self.client.post(
            reverse('billing:pay_receivable', args=[self.installments[0].pk]),
            payload,
        )
        self.client.post(
            reverse('billing:pay_receivable', args=[self.installments[0].pk]),
            payload,
        )

        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(FinancialMovement.objects.count(), 1)


class MultiPayViewReceiptTests(ReceiptViewTestCase):
    """Selecting titles across rentals emits one receipt per rental."""

    def setUp(self):
        super().setUp()
        self.other_rental = Rental.objects.create(
            number=501,
            customer=self.customer,
            pickup_date=self.today + timedelta(days=30),
            return_date=self.today + timedelta(days=40),
            total_value=Decimal('100.00'),
        )
        self.other_receivable = Receivable.objects.create(
            rental=self.other_rental,
            due_date=self.today,
            amount=Decimal('100.00'),
        )

    def test_titles_of_two_rentals_produce_two_receipts(self):
        response = self.client.post(
            reverse('billing:multi_pay', args=[self.customer.pk]),
            {
                'total_amount': '160,00',
                'payment_date': self.today.strftime('%d/%m/%Y'),
                'method': 'cash',
                'notes': '',
                'receivable_ids': [
                    str(self.installments[0].pk),
                    str(self.other_receivable.pk),
                ],
                'submission_token': str(uuid.uuid4()),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'um por locação')
        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 2)
        self.assertEqual(
            sum(receipt.amount for receipt in Receipt.objects.all()),
            Decimal('160.00'),
        )
        for receipt in Receipt.objects.all():
            self.assertEqual(
                receipt.amount,
                receipt.financial_movement.amount,
            )

    def test_titles_of_one_rental_produce_a_single_receipt(self):
        self.client.post(
            reverse('billing:multi_pay', args=[self.customer.pk]),
            {
                'total_amount': '120,00',
                'payment_date': self.today.strftime('%d/%m/%Y'),
                'method': 'cash',
                'notes': '',
                'receivable_ids': [
                    str(self.installments[0].pk),
                    str(self.installments[1].pk),
                ],
                'submission_token': str(uuid.uuid4()),
            },
        )

        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(ReceiptAllocation.objects.count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 1)

    def test_double_submit_with_the_same_token_registers_the_money_once(self):
        token = str(uuid.uuid4())
        payload = {
            'total_amount': '160,00',
            'payment_date': self.today.strftime('%d/%m/%Y'),
            'method': 'cash',
            'notes': '',
            'receivable_ids': [
                str(self.installments[0].pk),
                str(self.other_receivable.pk),
            ],
            'submission_token': token,
        }

        self.client.post(
            reverse('billing:multi_pay', args=[self.customer.pk]), payload,
        )
        self.client.post(
            reverse('billing:multi_pay', args=[self.customer.pk]), payload,
        )

        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 2)


class PaymentReportGroupingTests(ReceiptViewTestCase):
    """The report counts cash acts, not the titles each act happened to touch."""

    def test_a_four_title_receipt_is_one_report_row(self):
        self._pay(self.installments[0], '200,00')

        response = self.client.get(reverse('billing:payment_report'))

        self.assertEqual(response.status_code, 200)
        rows = response.context['cash_acts']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['amount'], Decimal('200.00'))
        self.assertEqual(rows[0]['title_count'], 4)
        self.assertEqual(response.context['total_received'], Decimal('200.00'))

    def test_legacy_payments_without_a_receipt_stay_one_row_each(self):
        Payment.objects.create(
            receivable=self.installments[0],
            customer=self.customer,
            rental=self.rental,
            payment_date=self.today,
            amount=Decimal('10.00'),
            method='cash',
            user=self.user,
        )
        Payment.objects.create(
            receivable=self.installments[1],
            customer=self.customer,
            rental=self.rental,
            payment_date=self.today,
            amount=Decimal('20.00'),
            method='cash',
            user=self.user,
        )

        response = self.client.get(reverse('billing:payment_report'))

        self.assertEqual(len(response.context['cash_acts']), 2)
        self.assertEqual(response.context['total_received'], Decimal('30.00'))

    def test_report_offers_the_reversal_entry_point(self):
        self._pay(self.installments[0], '200,00')

        response = self.client.get(reverse('billing:payment_report'))

        payment = Receipt.objects.get().allocations.order_by('pk').first().payment
        self.assertContains(response, 'Estornar')
        self.assertContains(
            response,
            reverse('billing:reverse_payment', args=[payment.pk]),
        )
