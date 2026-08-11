"""Tests for Sprint R5 — financial dashboard, receivables, payment flows."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from billing.models import (
    CashAccount,
    FinancialMovement,
    Payment,
    Receivable,
    Receipt,
)
from billing.services import financial_kpis, register_payment, reverse_payment
from company.models import Company
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()


def _make_scenario():
    """Create minimal Customer + Company + CashAccount + Rental + Receivable."""
    Company.objects.filter(pk=1).delete()
    company = Company.objects.create(
        name='Noivas Cia', last_rental_number=1, daily_interest_rate=Decimal('1.00')
    )
    customer = Customer.objects.create(name='Maria Silva', city='Recife')
    rental = Rental.objects.create(
        number=100, customer=customer,
        pickup_date=date(2026, 6, 1), return_date=date(2026, 6, 10),
        total_value=Decimal('300'),
    )
    account = CashAccount.objects.create(name='Caixa', active=True)
    receivable = Receivable.objects.create(
        rental=rental, due_date=date(2026, 6, 15), amount=Decimal('300'),
    )
    return customer, rental, receivable, account


class RegisterPaymentServiceTests(TestCase):
    """R5.06 — register_payment creates Payment + recalculates + creates FinancialMovement."""

    def setUp(self):
        self.customer, self.rental, self.rec, self.account = _make_scenario()

    def test_creates_payment_record(self):
        register_payment(self.rec, Decimal('100'), date(2026, 6, 20), method='cash')
        self.assertEqual(Payment.objects.count(), 1)
        p = Payment.objects.first()
        self.assertEqual(p.amount, Decimal('100'))
        self.assertEqual(p.receivable, self.rec)

    def test_recalculates_receivable_balance(self):
        register_payment(self.rec, Decimal('100'), date(2026, 6, 20))
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.paid_amount, Decimal('100'))
        self.assertEqual(self.rec.balance, Decimal('200'))

    def test_interest_is_cash_but_does_not_reduce_principal(self):
        register_payment(
            self.rec,
            Decimal('110.00'),
            date(2026, 6, 20),
            interest_amount=Decimal('10.00'),
        )

        self.rec.refresh_from_db()
        self.assertEqual(self.rec.paid_amount, Decimal('100.00'))
        self.assertEqual(self.rec.balance, Decimal('200.00'))
        self.assertEqual(FinancialMovement.objects.get().amount, Decimal('110.00'))

    def test_discount_reduces_principal_without_increasing_cash(self):
        register_payment(
            self.rec,
            Decimal('90.00'),
            date(2026, 6, 20),
            discount_amount=Decimal('10.00'),
        )

        self.rec.refresh_from_db()
        self.assertEqual(self.rec.paid_amount, Decimal('100.00'))
        self.assertEqual(self.rec.balance, Decimal('200.00'))
        self.assertEqual(FinancialMovement.objects.get().amount, Decimal('90.00'))

    def test_creates_financial_movement_inflow(self):
        payment = register_payment(self.rec, Decimal('150'), date(2026, 6, 20))
        mv = FinancialMovement.objects.first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.direction, FinancialMovement.Direction.INFLOW)
        self.assertEqual(mv.amount, Decimal('150'))
        self.assertEqual(mv.source, FinancialMovement.Source.PAYMENT)
        self.assertEqual(mv.account, self.account)
        self.assertEqual(mv.payment, payment)

    def test_rolls_back_payment_when_movement_fails(self):
        with mock.patch(
            'billing.services.FinancialMovement.objects.create',
            side_effect=RuntimeError('movement failed'),
        ):
            with self.assertRaises(RuntimeError):
                register_payment(self.rec, Decimal('150'), date(2026, 6, 20))

        self.assertEqual(Payment.objects.count(), 0)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.paid_amount, Decimal('0'))
        self.assertEqual(self.rec.balance, Decimal('300'))

    def test_requires_an_active_cash_account_before_creating_payment(self):
        self.account.active = False
        self.account.save()

        with self.assertRaisesMessage(
            ValueError,
            'Não é possível registrar recebimento sem uma conta caixa ativa. '
            'Configure uma conta em Financeiro > Contas.',
        ):
            register_payment(self.rec, Decimal('150'), date(2026, 6, 20))

        self.assertFalse(Payment.objects.exists())
        self.assertFalse(FinancialMovement.objects.exists())
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.paid_amount, Decimal('0'))
        self.assertEqual(self.rec.balance, Decimal('300'))

    def test_links_payment_to_customer_and_rental(self):
        register_payment(self.rec, Decimal('50'), date(2026, 6, 20))
        p = Payment.objects.first()
        self.assertEqual(p.customer, self.customer)
        self.assertEqual(p.rental, self.rental)

    def test_partial_payment_balance_non_zero(self):
        register_payment(self.rec, Decimal('50'), date(2026, 6, 20))
        self.rec.refresh_from_db()
        self.assertFalse(self.rec.is_paid)
        self.assertEqual(self.rec.balance, Decimal('250'))

    def test_full_payment_marks_paid(self):
        register_payment(self.rec, Decimal('300'), date(2026, 6, 20))
        self.rec.refresh_from_db()
        self.assertTrue(self.rec.is_paid)

    def test_overpayment_is_rejected_without_negative_balance(self):
        with self.assertRaisesMessage(
            ValueError,
            'O valor aplicado ao principal não pode superar o saldo do título.',
        ):
            register_payment(self.rec, Decimal('400'), date(2026, 6, 20))

        self.rec.refresh_from_db()
        self.assertEqual(self.rec.balance, Decimal('300'))
        self.assertFalse(Payment.objects.exists())

    def test_rejects_invalid_service_inputs(self):
        with self.assertRaises(ValueError):
            register_payment(
                self.rec,
                Decimal('-1'),
                date(2026, 6, 20),
            )
        with self.assertRaises(ValueError):
            register_payment(
                self.rec,
                Decimal('1'),
                date(2026, 6, 20),
                method='invalid',
            )
        with self.assertRaises(ValueError):
            register_payment(
                self.rec,
                Decimal('1'),
                timezone.localdate() + timedelta(days=1),
            )

        self.assertEqual(Payment.objects.count(), 0)


class ReversePaymentServiceTests(TestCase):
    """R5.09 — reverse_payment creates reversal + outflow movement."""

    def setUp(self):
        self.customer, self.rental, self.rec, self.account = _make_scenario()
        self.payment = register_payment(self.rec, Decimal('200'), date(2026, 6, 20))

    def test_creates_reversal_payment_negative(self):
        reverse_payment(self.payment, reason='Cheque sem fundos')
        self.assertEqual(Payment.objects.count(), 2)
        reversal = Payment.objects.filter(is_reversal=True).first()
        self.assertEqual(reversal.amount, Decimal('-200'))

    def test_original_payment_linked_to_reversal(self):
        reverse_payment(self.payment, reason='Motivo')
        self.payment.refresh_from_db()
        self.assertIsNotNone(self.payment.reversed_by)

    def test_receivable_balance_restored(self):
        reverse_payment(self.payment, reason='Motivo')
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.balance, Decimal('300'))

    def test_creates_outflow_financial_movement(self):
        reversal = reverse_payment(self.payment, reason='Motivo')
        outflow = FinancialMovement.objects.filter(
            direction=FinancialMovement.Direction.OUTFLOW
        ).first()
        self.assertIsNotNone(outflow)
        self.assertEqual(outflow.amount, Decimal('200'))
        self.assertEqual(outflow.source, FinancialMovement.Source.REVERSAL)
        self.assertEqual(outflow.payment, reversal)

    def test_reversal_uses_original_account_even_when_inactive(self):
        self.account.active = False
        self.account.save(update_fields=['active', 'updated_at'])

        reverse_payment(self.payment, reason='Motivo')

        outflow = FinancialMovement.objects.get(
            direction=FinancialMovement.Direction.OUTFLOW,
        )
        self.assertEqual(outflow.account, self.account)

    def test_reversal_refuses_missing_original_movement(self):
        FinancialMovement.objects.filter(payment=self.payment).delete()

        with self.assertRaisesMessage(
            ValueError,
            'O recebimento precisa ter exatamente um movimento de entrada',
        ):
            reverse_payment(self.payment, reason='Motivo')

        self.assertEqual(Payment.objects.count(), 1)
        self.payment.refresh_from_db()
        self.assertIsNone(self.payment.reversed_by)

    def test_database_refuses_duplicate_payment_movements(self):
        original = FinancialMovement.objects.get(payment=self.payment)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FinancialMovement.objects.create(
                    date=original.date,
                    account=original.account,
                    direction=original.direction,
                    amount=original.amount,
                    source=original.source,
                    customer=original.customer,
                    receivable=original.receivable,
                    payment=self.payment,
                    rental=original.rental,
                )

        self.assertEqual(Payment.objects.count(), 1)

    def test_reversal_refuses_movement_with_divergent_amount(self):
        FinancialMovement.objects.filter(payment=self.payment).update(
            amount=Decimal('199.99'),
        )

        with self.assertRaisesMessage(
            ValueError,
            'O movimento de entrada diverge do recebimento',
        ):
            reverse_payment(self.payment, reason='Motivo')

        self.assertEqual(Payment.objects.count(), 1)

    def test_rolls_back_reversal_when_movement_fails(self):
        with mock.patch(
            'billing.services.FinancialMovement.objects.create',
            side_effect=RuntimeError('movement failed'),
        ):
            with self.assertRaises(RuntimeError):
                reverse_payment(self.payment, reason='Motivo')

        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(
            FinancialMovement.objects.filter(direction=FinancialMovement.Direction.OUTFLOW).count(),
            0,
        )
        self.payment.refresh_from_db()
        self.rec.refresh_from_db()
        self.assertIsNone(self.payment.reversed_by)
        self.assertEqual(self.rec.balance, Decimal('100'))


class FinancialKPIsTests(TestCase):
    """R5.02 — financial_kpis returns correct aggregates."""

    def setUp(self):
        self.customer, self.rental, self.rec, self.account = _make_scenario()

    def test_open_balance_counts_open_receivables(self):
        kpis = financial_kpis(today=date(2026, 6, 10))
        self.assertEqual(kpis['open_balance'], Decimal('300'))
        self.assertEqual(kpis['open_count'], 1)

    def test_overdue_when_past_due(self):
        kpis = financial_kpis(today=date(2026, 6, 20))
        self.assertGreater(kpis['overdue_balance'], 0)
        self.assertEqual(kpis['overdue_count'], 1)

    def test_received_today_sums_payments(self):
        today = date(2026, 6, 20)
        register_payment(self.rec, Decimal('100'), today)
        kpis = financial_kpis(today=today)
        self.assertEqual(kpis['received_today'], Decimal('100'))

    def test_received_today_excludes_reversals(self):
        today = date(2026, 6, 20)
        p = register_payment(self.rec, Decimal('100'), today)
        reverse_payment(p, reason='Teste')
        kpis = financial_kpis(today=today)
        self.assertEqual(kpis['received_today'], Decimal('0'))

    def test_zero_counts_when_no_receivables(self):
        Receivable.objects.all().delete()
        kpis = financial_kpis(today=date(2026, 6, 10))
        self.assertEqual(kpis['open_balance'], Decimal('0'))
        self.assertEqual(kpis['open_count'], 0)


class FinancialDashboardViewTests(TestCase):
    """R5.01, R5.02, R5.03 — dashboard accessible to billing users."""

    def setUp(self):
        self.user = User.objects.create_user(email='fin@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)
        self.client.force_login(self.user)
        _make_scenario()

    def test_dashboard_returns_200(self):
        response = self.client.get(reverse('billing:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_shows_kpi_labels(self):
        response = self.client.get(reverse('billing:dashboard'))
        self.assertContains(response, 'Em aberto')
        self.assertContains(response, 'Vencidos')
        self.assertContains(response, 'Recebido hoje')

    def test_dashboard_requires_billing_module(self):
        user2 = User.objects.create_user(email='nofin@b.com', password='Senha12345')
        self.client.force_login(user2)
        response = self.client.get(reverse('billing:dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_sidebar_contains_financeiro_link(self):
        response = self.client.get(reverse('billing:dashboard'))
        self.assertContains(response, 'Financeiro')


class GlobalReceivableListViewTests(TestCase):
    """R5.04 — global receivables list with filters."""

    def setUp(self):
        self.user = User.objects.create_user(email='fin@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)
        self.client.force_login(self.user)
        self.customer, self.rental, self.rec, self.account = _make_scenario()

    def test_list_returns_200(self):
        response = self.client.get(reverse('billing:receivables'))
        self.assertEqual(response.status_code, 200)

    def test_list_shows_open_by_default(self):
        response = self.client.get(reverse('billing:receivables'))
        self.assertContains(response, 'MARIA SILVA')

    def test_filter_by_status_paid_hides_open(self):
        response = self.client.get(reverse('billing:receivables') + '?status=paid')
        self.assertNotContains(response, 'Maria Silva')

    def test_filter_by_customer_name(self):
        Customer.objects.create(name='Carlos Lima')
        response = self.client.get(reverse('billing:receivables') + '?q=Carlos')
        self.assertNotContains(response, 'Maria Silva')

    def test_overdue_filter(self):
        response = self.client.get(reverse('billing:receivables') + '?overdue=1')
        self.assertEqual(response.status_code, 200)

    def test_list_loads_company_once_for_interest_rows(self):
        company = Company.load()
        for number in (101, 102):
            rental = Rental.objects.create(
                number=number, customer=self.rec.rental.customer,
                pickup_date=date(2026, 6, 1), return_date=date(2026, 6, 10),
                total_value=Decimal('100'),
            )
            Receivable.objects.create(
                rental=rental, due_date=date(2020, 1, 1), amount=Decimal('100'),
            )

        with mock.patch('billing.views.Company.load', return_value=company) as load:
            response = self.client.get(reverse('billing:receivables'))

        self.assertEqual(response.status_code, 200)
        load.assert_called_once()


class CustomerReceivableViewTests(TestCase):
    """R5.05 — receivables by customer."""

    def setUp(self):
        self.user = User.objects.create_user(email='fin@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)
        self.client.force_login(self.user)
        self.customer, self.rental, self.rec, self.account = _make_scenario()

    def test_search_returns_results(self):
        response = self.client.get(
            reverse('billing:customer_receivables_search') + '?q=Maria'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MARIA SILVA')

    def test_customer_view_shows_receivables(self):
        response = self.client.get(
            reverse('billing:customer_receivables', args=[self.customer.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MARIA SILVA')

    def test_customer_view_shows_total_balance(self):
        response = self.client.get(
            reverse('billing:customer_receivables', args=[self.customer.pk])
        )
        self.assertContains(response, '300')


class ReceivablePayViewTests(TestCase):
    """R5.06, R5.08 — single receivable payment form."""

    def setUp(self):
        self.user = User.objects.create_user(email='fin@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='billing.receive', allowed=True)
        self.client.force_login(self.user)
        self.customer, self.rental, self.rec, self.account = _make_scenario()

    def test_pay_form_returns_200(self):
        response = self.client.get(
            reverse('billing:pay_receivable', args=[self.rec.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_post_payment_creates_payment_and_movement(self):
        response = self.client.post(
            reverse('billing:pay_receivable', args=[self.rec.pk]),
            {
                'amount': '150.00',
                'payment_date': '2026-06-20',
                'method': 'cash',
                'interest_amount': '0',
                'discount_amount': '0',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        # One cash act: a receipt holding a single allocation, its Payment and
        # the one inflow movement that receipt owns.
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(FinancialMovement.objects.count(), 1)

    def test_post_without_active_cash_account_shows_error_and_saves_nothing(self):
        self.account.active = False
        self.account.save()

        response = self.client.post(
            reverse('billing:pay_receivable', args=[self.rec.pk]),
            {
                'amount': '150.00',
                'payment_date': '2026-06-20',
                'method': 'cash',
                'interest_amount': '0',
                'discount_amount': '0',
                'notes': '',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Não é possível registrar recebimento sem uma conta caixa ativa. '
            'Configure uma conta em Financeiro &gt; Contas.',
        )
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(FinancialMovement.objects.exists())

    def test_overpayment_is_rejected(self):
        response = self.client.post(
            reverse('billing:pay_receivable', args=[self.rec.pk]),
            {
                'amount': '500.00',  # > 300 balance
                'payment_date': '2026-06-20',
                'method': 'cash',
                'interest_amount': '0',
                'discount_amount': '0',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'O valor não pode superar o total com juros')
        self.assertEqual(Payment.objects.count(), 0)

    def test_forged_overpayment_confirmation_does_not_bypass_cap(self):
        response = self.client.post(
            reverse('billing:pay_receivable', args=[self.rec.pk]),
            {
                'amount': '500.00',
                'payment_date': '2026-06-20',
                'method': 'cash',
                'interest_amount': '0',
                'discount_amount': '0',
                'notes': '',
                'confirm_overpayment': 'on',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.objects.count(), 0)


class MultiPayViewTests(TestCase):
    """R5.07 — multi-receivable payment."""

    def setUp(self):
        self.user = User.objects.create_user(email='fin@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='billing.receive', allowed=True)
        self.client.force_login(self.user)
        self.customer, self.rental, self.rec, self.account = _make_scenario()
        rental2 = Rental.objects.create(
            number=101, customer=self.customer,
            pickup_date=date(2026, 6, 1), return_date=date(2026, 6, 10),
            total_value=Decimal('200'),
        )
        self.rec2 = Receivable.objects.create(
            rental=rental2, due_date=date(2026, 6, 20), amount=Decimal('200'),
        )

    def test_multi_pay_get_returns_200(self):
        response = self.client.get(
            reverse('billing:multi_pay', args=[self.customer.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_multi_pay_distributes_payment(self):
        self.client.post(
            reverse('billing:multi_pay', args=[self.customer.pk]),
            {
                'total_amount': '500.00',  # 300 + 200
                'payment_date': '2026-06-20',
                'method': 'cash',
                'notes': '',
                'receivable_ids': [str(self.rec.pk), str(self.rec2.pk)],
            },
        )
        self.rec.refresh_from_db()
        self.rec2.refresh_from_db()
        self.assertTrue(self.rec.is_paid)
        self.assertTrue(self.rec2.is_paid)
        self.assertEqual(Payment.objects.count(), 2)
        # The two titles belong to different rentals, and a receipt is bound to
        # one rental, so the act is recorded as one receipt per rental.
        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(FinancialMovement.objects.count(), 2)

    def test_multi_pay_partial_amount_covers_first(self):
        self.client.post(
            reverse('billing:multi_pay', args=[self.customer.pk]),
            {
                'total_amount': '300.00',
                'payment_date': '2026-06-20',
                'method': 'cash',
                'notes': '',
                'receivable_ids': [str(self.rec.pk), str(self.rec2.pk)],
            },
        )
        self.rec.refresh_from_db()
        self.rec2.refresh_from_db()
        self.assertTrue(self.rec.is_paid)
        self.assertFalse(self.rec2.is_paid)


class PaymentReversalViewTests(TestCase):
    """R5.09 — payment reversal view."""

    def setUp(self):
        self.user = User.objects.create_user(email='fin@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='billing.reverse', allowed=True)
        self.client.force_login(self.user)
        self.customer, self.rental, self.rec, self.account = _make_scenario()
        self.payment = register_payment(self.rec, Decimal('100'), date(2026, 6, 20))

    def test_reversal_form_returns_200(self):
        response = self.client.get(
            reverse('billing:reverse_payment', args=[self.payment.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_reversal_creates_outflow(self):
        self.client.post(
            reverse('billing:reverse_payment', args=[self.payment.pk]),
            {'reason': 'Cheque devolvido'},
        )
        outflow = FinancialMovement.objects.filter(
            direction=FinancialMovement.Direction.OUTFLOW
        ).first()
        self.assertIsNotNone(outflow)

    def test_cannot_reverse_already_reversed(self):
        reverse_payment(self.payment, reason='Motivo')
        self.payment.refresh_from_db()
        response = self.client.get(
            reverse('billing:reverse_payment', args=[self.payment.pk])
        )
        # Should redirect with error, not return 200
        self.assertEqual(response.status_code, 302)
