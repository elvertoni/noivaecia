"""Tests for Sprint R6 — cash movements, reports, reconciliation, penalty services."""

import csv
import io
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from billing.services import (
    compute_damage_penalty,
    compute_loss_penalty,
    compute_moratoria,
    compute_monthly_interest,
    reconcile_financial,
)
from company.models import Company
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()


def _make_scenario():
    """Minimal Customer + Company + CashAccount + Rental + Receivable."""
    Company.objects.filter(pk=1).delete()
    company = Company.objects.create(
        name='Noivas Cia',
        last_rental_number=1,
        daily_interest_rate=Decimal('1.00'),
        late_fee_rate=Decimal('2.00'),
        monthly_interest_rate=Decimal('3.00'),
        damage_penalty_rate=Decimal('50.00'),
        loss_penalty_rate=Decimal('100.00'),
    )
    customer = Customer.objects.create(name='Maria Silva', city='Recife')
    rental = Rental.objects.create(
        number=200, customer=customer,
        pickup_date=date(2026, 6, 1), return_date=date(2026, 6, 10),
        total_value=Decimal('400'),
    )
    account = CashAccount.objects.create(name='Caixa', active=True)
    receivable = Receivable.objects.create(
        rental=rental, due_date=date(2026, 6, 15), amount=Decimal('400'),
    )
    return customer, rental, receivable, account, company


def _make_staff(module_key='billing'):
    user = User.objects.create_user(email='staff@test.com', password='pass')
    ModulePermission.objects.create(user=user, module_key=module_key, allowed=True)
    return user


class ComputeMoratoriaTests(TestCase):
    def setUp(self):
        _, _, self.rec, _, _ = _make_scenario()

    def test_zero_if_not_overdue(self):
        result = compute_moratoria(self.rec, on_date=date(2026, 6, 14))
        self.assertEqual(result, Decimal('0.00'))

    def test_zero_if_due_date(self):
        result = compute_moratoria(self.rec, on_date=date(2026, 6, 15))
        self.assertEqual(result, Decimal('0.00'))

    def test_nonzero_when_overdue(self):
        result = compute_moratoria(self.rec, on_date=date(2026, 6, 20))
        # balance=400, rate=2% => 8.00
        self.assertEqual(result, Decimal('8.00'))

    def test_zero_if_paid(self):
        # balance = amount - paid_amount; set paid_amount=amount so balance=0
        self.rec.paid_amount = self.rec.amount
        self.rec.save()
        result = compute_moratoria(self.rec, on_date=date(2026, 6, 20))
        self.assertEqual(result, Decimal('0.00'))


class ComputeMonthlyInterestTests(TestCase):
    def setUp(self):
        _, _, self.rec, _, _ = _make_scenario()

    def test_zero_if_not_overdue(self):
        result = compute_monthly_interest(self.rec, on_date=date(2026, 6, 14))
        self.assertEqual(result, Decimal('0.00'))

    def test_interest_calculated_daily(self):
        # 30 days overdue; monthly_rate=3 => daily=3/30=0.1%; balance=400 => 1.20/day * 30 = 12.00... no:
        # interest = 400 * (0.1/100) * 30 = 400 * 0.001 * 30 = 12.00
        result = compute_monthly_interest(self.rec, on_date=date(2026, 7, 15))
        self.assertEqual(result, Decimal('12.00'))

    def test_fallback_to_daily_when_monthly_zero(self):
        Company.objects.update(monthly_interest_rate=Decimal('0'))
        # daily_rate=1%; 10 days; balance=400 => 40.00
        result = compute_monthly_interest(self.rec, on_date=date(2026, 6, 25))
        self.assertEqual(result, Decimal('40.00'))


class ComputePenaltyTests(TestCase):
    def setUp(self):
        _make_scenario()

    def test_damage_penalty(self):
        result = compute_damage_penalty(Decimal('200'))
        # 50% of 200 = 100
        self.assertEqual(result, Decimal('100.00'))

    def test_loss_penalty(self):
        result = compute_loss_penalty(Decimal('150'))
        # 100% of 150 = 150
        self.assertEqual(result, Decimal('150.00'))


class ReconcileFinancialTests(TestCase):
    def setUp(self):
        self.customer, self.rental, self.rec, self.account, _ = _make_scenario()

    def test_empty_db_returns_zeros(self):
        Receivable.objects.all().delete()
        result = reconcile_financial()
        self.assertEqual(result['total_receivable_amount'], Decimal('0'))
        self.assertEqual(result['inconsistent_count'], 0)
        self.assertEqual(result['payments_without_movement_count'], 0)

    def test_detects_inconsistent_balance(self):
        # Manually force paid_amount to a wrong value
        self.rec.paid_amount = Decimal('50')
        self.rec.save()
        # No Payment record exists => payment_sum=0, stored=50 => diff=50
        result = reconcile_financial()
        self.assertEqual(result['inconsistent_count'], 1)
        row = result['inconsistent_balances'][0]
        self.assertEqual(row['diff'], Decimal('50'))

    def test_consistent_when_aligned(self):
        # paid_amount=0 (default), no payments => consistent for open rec
        result = reconcile_financial()
        self.assertEqual(result['inconsistent_count'], 0)

    def test_paid_no_payments_counts_closed_legacy(self):
        self.rec.balance = Decimal('0')
        self.rec.paid_amount = Decimal('400')
        self.rec.save()
        # No Payment objects; balance<=0 and no payments => counted
        result = reconcile_financial()
        self.assertEqual(result['paid_no_payments_count'], 1)

    def test_payments_without_movement_counts_before_sampling_ids(self):
        Payment.objects.bulk_create([
            Payment(
                receivable=self.rec,
                customer=self.customer,
                rental=self.rental,
                payment_date=date(2026, 6, 20),
                amount=Decimal('1'),
            )
            for _ in range(201)
        ])

        result = reconcile_financial()
        self.assertEqual(result['payments_without_movement_count'], 201)

    def test_payments_without_movement_uses_payment_link(self):
        first = Payment.objects.create(
            receivable=self.rec,
            customer=self.customer,
            rental=self.rental,
            payment_date=date(2026, 6, 20),
            amount=Decimal('100'),
        )
        Payment.objects.create(
            receivable=self.rec,
            customer=self.customer,
            rental=self.rental,
            payment_date=date(2026, 6, 21),
            amount=Decimal('100'),
        )
        FinancialMovement.objects.create(
            date=date(2026, 6, 20),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('100'),
            source=FinancialMovement.Source.PAYMENT,
            receivable=self.rec,
            payment=first,
        )

        result = reconcile_financial()
        self.assertEqual(result['payments_without_movement_count'], 1)


class CashMovementListViewTests(TestCase):
    def setUp(self):
        _, _, _, self.account, _ = _make_scenario()
        self.user = _make_staff('billing')
        self.client.force_login(self.user)
        FinancialMovement.objects.create(
            date=date(2026, 6, 10),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('100'),
            description='Teste entrada',
            source=FinancialMovement.Source.MANUAL,
        )

    def test_200_no_filters(self):
        response = self.client.get(reverse('billing:cash_movements'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Teste entrada')

    def test_filter_by_direction(self):
        response = self.client.get(reverse('billing:cash_movements'), {'direction': 'inflow'})
        self.assertEqual(response.status_code, 200)

    def test_context_has_totals(self):
        response = self.client.get(reverse('billing:cash_movements'))
        self.assertIn('total_inflow', response.context)
        self.assertIn('total_outflow', response.context)
        self.assertIn('balance', response.context)

    def test_redirect_if_no_permission(self):
        user2 = User.objects.create_user(email='noaccess@test.com', password='pass')
        self.client.force_login(user2)
        response = self.client.get(reverse('billing:cash_movements'))
        self.assertNotEqual(response.status_code, 200)


class ManualCashMovementViewTests(TestCase):
    def setUp(self):
        _, _, _, self.account, _ = _make_scenario()
        self.user = _make_staff('billing')
        ActionPermission.objects.create(user=self.user, action_key='billing.cash', allowed=True)
        self.client.force_login(self.user)

    def test_get_form(self):
        response = self.client.get(reverse('billing:new_movement'))
        self.assertEqual(response.status_code, 200)

    def test_creates_movement(self):
        data = {
            'date': '2026-06-10',
            'account': self.account.pk,
            'direction': 'inflow',
            'amount': '150.00',
            'description': 'Pagamento avulso',
            'customer_name': '',
        }
        response = self.client.post(reverse('billing:new_movement'), data)
        self.assertRedirects(response, reverse('billing:cash_movements'))
        self.assertEqual(FinancialMovement.objects.count(), 1)
        mv = FinancialMovement.objects.first()
        self.assertEqual(mv.source, FinancialMovement.Source.MANUAL)
        self.assertEqual(mv.amount, Decimal('150.00'))


class PaymentReportViewTests(TestCase):
    def setUp(self):
        self.customer, self.rental, self.rec, self.account, _ = _make_scenario()
        self.user = _make_staff('billing')
        self.client.force_login(self.user)
        Payment.objects.create(
            receivable=self.rec,
            customer=self.customer,
            rental=self.rental,
            payment_date=date(2026, 6, 20),
            amount=Decimal('200'),
            method='cash',
            user=self.user,
        )

    def test_200_renders(self):
        response = self.client.get(reverse('billing:payment_report'))
        self.assertEqual(response.status_code, 200)

    def test_total_received_in_context(self):
        response = self.client.get(reverse('billing:payment_report'))
        self.assertEqual(response.context['total_received'], Decimal('200'))

    def test_filter_by_date_excludes(self):
        response = self.client.get(
            reverse('billing:payment_report'), {'date_from': '2026-07-01'}
        )
        self.assertEqual(response.context['total_received'], Decimal('0'))


class CashMovementReportViewTests(TestCase):
    def setUp(self):
        _, _, _, self.account, _ = _make_scenario()
        self.user = _make_staff('billing')
        self.client.force_login(self.user)

    def test_200_default_period(self):
        response = self.client.get(reverse('billing:cash_report'))
        self.assertEqual(response.status_code, 200)

    def test_context_keys(self):
        response = self.client.get(reverse('billing:cash_report'))
        for key in ('total_inflow', 'total_outflow', 'balance', 'source_breakdown', 'movements'):
            self.assertIn(key, response.context)


class ReconciliationViewTests(TestCase):
    def setUp(self):
        _, _, _, _, _ = _make_scenario()
        self.user = _make_staff('billing')
        self.client.force_login(self.user)

    def test_200_renders(self):
        response = self.client.get(reverse('billing:reconciliation'))
        self.assertEqual(response.status_code, 200)

    def test_recon_dict_in_context(self):
        response = self.client.get(reverse('billing:reconciliation'))
        recon = response.context['recon']
        self.assertIn('total_receivable_amount', recon)
        self.assertIn('inconsistent_count', recon)


class ReconciliationExportViewTests(TestCase):
    def setUp(self):
        self.customer, self.rental, self.rec, self.account, _ = _make_scenario()
        # Force inconsistency
        self.rec.paid_amount = Decimal('99')
        self.rec.save()
        self.user = _make_staff('billing')
        self.client.force_login(self.user)

    def test_returns_csv(self):
        response = self.client.get(reverse('billing:reconciliation_export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])

    def test_csv_has_header_row(self):
        response = self.client.get(reverse('billing:reconciliation_export'))
        content = response.content.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(content), delimiter=';')
        headers = next(reader)
        self.assertIn('Diferença', headers)

    def test_csv_has_inconsistent_row(self):
        response = self.client.get(reverse('billing:reconciliation_export'))
        content = response.content.decode('utf-8-sig')
        # More than just the header row
        rows = list(csv.reader(io.StringIO(content)))
        self.assertGreater(len(rows), 1)
