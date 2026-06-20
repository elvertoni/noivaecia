"""Tests for Sprint R9 — customer search, history, inactivation, deletion guard."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from billing.models import CashAccount, Payment, Receivable
from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer
from rentals.models import Rental, RentalItem

User = get_user_model()


def _make_user(module_key='customers'):
    user = User.objects.create_user(email='r9@test.com', password='pass')
    ModulePermission.objects.create(user=user, module_key=module_key, allowed=True)
    return user


def _make_customer(**kwargs):
    defaults = {'name': 'Maria Silva', 'city': 'Recife', 'cpf': '123.456.789-00',
                'rg': 'AB123456', 'phone_home': '(81)3333-1111',
                'phone_mobile': '(81)99999-1111'}
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def _make_rental(customer, number=100):
    Company.objects.filter(pk=1).delete()
    Company.objects.create(name='T', last_rental_number=number)
    rental = Rental.objects.create(
        number=number, customer=customer,
        pickup_date=date(2026, 6, 1), return_date=date(2026, 6, 10),
        total_value=Decimal('200'),
    )
    return rental


def _make_receivable(rental, amount=Decimal('200')):
    return Receivable.objects.create(
        rental=rental, due_date=date(2026, 6, 30),
        amount=amount, balance=amount,
    )


# ── R9.01 Customer list search ────────────────────────────────────────────────

class CustomerListSearchTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('customers:list')
        self.c1 = _make_customer(name='Ana Lima', cpf='111.000.000-00', rg='RG999',
                                 phone_home='(11)2222-3333', phone_mobile='(11)99999-4444')
        # Explicit non-colliding digits: the default mobile contains "999",
        # which would otherwise match the RG-digit search below by phone.
        self.c2 = _make_customer(name='Bruno Costa', legacy_id=42,
                                 phone_home='(11)2000-1000', phone_mobile='(11)98888-7777')

    def test_search_by_name(self):
        r = self.client.get(self.url, {'q': 'Ana'})
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.c1.pk, pks)
        self.assertNotIn(self.c2.pk, pks)

    def test_search_by_cpf(self):
        r = self.client.get(self.url, {'q': '111.000'})
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.c1.pk, pks)

    def test_search_by_rg(self):
        r = self.client.get(self.url, {'q': 'RG999'})
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.c1.pk, pks)
        self.assertNotIn(self.c2.pk, pks)

    def test_search_by_rg_digits_matches_masked_rg(self):
        customer = _make_customer(name='Clara RG', rg='12.345.678-9')

        r = self.client.get(self.url, {'q': '123456789'})

        pks = {c.pk for c in r.context['customers']}
        self.assertIn(customer.pk, pks)

    def test_search_by_masked_rg_matches_digits_rg(self):
        customer = _make_customer(name='Dora RG', rg='123456789')

        r = self.client.get(self.url, {'q': '12.345.678-9'})

        pks = {c.pk for c in r.context['customers']}
        self.assertIn(customer.pk, pks)

    def test_quick_search_by_rg_digits_matches_masked_rg(self):
        customer = _make_customer(name='Eva RG', rg='98.765.432-1')

        r = self.client.get(reverse('customers:search'), {'q': '987654321'})

        self.assertEqual(r.status_code, 200)
        ids = {row['id'] for row in r.json()['results']}
        self.assertIn(customer.pk, ids)

    def test_search_by_phone_home(self):
        r = self.client.get(self.url, {'q': '2222-3333'})
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.c1.pk, pks)

    def test_search_by_phone_mobile(self):
        r = self.client.get(self.url, {'q': '99999-4444'})
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.c1.pk, pks)

    def test_search_by_legacy_id(self):
        r = self.client.get(self.url, {'q': '42'})
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.c2.pk, pks)


# ── R9.07 Active/inactive filter ──────────────────────────────────────────────

class CustomerActiveFilterTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('customers:list')
        self.active = _make_customer(name='Ativa', is_active=True)
        self.inactive = _make_customer(name='Inativa', is_active=False)

    def test_default_shows_active_only(self):
        r = self.client.get(self.url, {'active': '1'})
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.active.pk, pks)
        self.assertNotIn(self.inactive.pk, pks)

    def test_inactive_filter(self):
        r = self.client.get(self.url, {'active': '0'})
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.inactive.pk, pks)
        self.assertNotIn(self.active.pk, pks)

    def test_no_filter_shows_all(self):
        r = self.client.get(self.url)
        pks = {c.pk for c in r.context['customers']}
        self.assertIn(self.active.pk, pks)
        self.assertIn(self.inactive.pk, pks)


# ── R9.02 Customer detail / history ──────────────────────────────────────────

class CustomerDetailViewTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)
        self.customer = _make_customer()
        self.rental = _make_rental(self.customer)
        self.rec = _make_receivable(self.rental)
        self.url = reverse('customers:detail', args=[self.customer.pk])

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_context_has_rentals(self):
        r = self.client.get(self.url)
        self.assertIn(self.rental, r.context['rentals'])

    def test_rentals_are_paginated(self):
        for number in range(101, 131):
            Rental.objects.create(
                number=number,
                customer=self.customer,
                pickup_date=date(2026, 6, 1),
                return_date=date(2026, 6, 10),
                total_value=Decimal('200'),
            )

        r = self.client.get(self.url)

        self.assertEqual(len(r.context['rentals']), 25)
        self.assertEqual(r.context['rentals_count'], 31)
        self.assertTrue(r.context['rentals_is_paginated'])

    def test_rental_pagination_preserves_filters(self):
        r = self.client.get(self.url, {'rental_status': 'pending', 'rental_page': '2'})

        self.assertEqual(r.context['rental_status'], 'pending')
        self.assertEqual(r.context['rental_page_querystring'], 'rental_status=pending&')

    def test_context_has_receivables(self):
        r = self.client.get(self.url)
        self.assertIn(self.rec, r.context['receivables'])


# ── R9.04 Financial summary ───────────────────────────────────────────────────

class CustomerFinancialSummaryTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)
        self.customer = _make_customer()
        self.rental = _make_rental(self.customer)
        self.rec = _make_receivable(self.rental, amount=Decimal('200'))

    def test_total_balance_in_context(self):
        r = self.client.get(reverse('customers:detail', args=[self.customer.pk]))
        self.assertEqual(r.context['total_balance'], Decimal('200'))

    def test_total_rented_excludes_cancelled(self):
        cancelled = Rental.objects.create(
            number=999, customer=self.customer,
            pickup_date=date(2026, 5, 1), return_date=date(2026, 5, 5),
            total_value=Decimal('500'), status='cancelled',
        )
        r = self.client.get(reverse('customers:detail', args=[self.customer.pk]))
        self.assertEqual(r.context['total_rented'], Decimal('200'))


# ── R9.03 History filters ─────────────────────────────────────────────────────

class CustomerHistoryFiltersTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)
        self.customer = _make_customer()
        self.rental = _make_rental(self.customer)
        self.rec = _make_receivable(self.rental)
        self.url = reverse('customers:detail', args=[self.customer.pk])

    def test_rental_status_filter(self):
        r = self.client.get(self.url, {'rental_status': 'pending'})
        self.assertIn(self.rental, r.context['rentals'])

    def test_rental_status_filter_excludes(self):
        r = self.client.get(self.url, {'rental_status': 'returned'})
        self.assertNotIn(self.rental, r.context['rentals'])

    def test_financial_open_filter(self):
        r = self.client.get(self.url, {'financial_status': 'open'})
        self.assertIn(self.rec, r.context['receivables'])

    def test_financial_paid_filter_excludes_open(self):
        r = self.client.get(self.url, {'financial_status': 'paid'})
        self.assertNotIn(self.rec, r.context['receivables'])


# ── R9.06 Delete guard ────────────────────────────────────────────────────────

class CustomerDeleteGuardTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        ActionPermission.objects.create(user=self.user, action_key='customers.delete', allowed=True)
        self.client.force_login(self.user)
        self.customer = _make_customer()
        self.rental = _make_rental(self.customer)

    def test_delete_blocked_when_has_rentals(self):
        url = reverse('customers:delete', args=[self.customer.pk])
        r = self.client.post(url)
        # Customer must still exist
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_delete_allowed_without_history(self):
        fresh = _make_customer(name='Sem Historico')
        url = reverse('customers:delete', args=[fresh.pk])
        r = self.client.post(url)
        self.assertFalse(Customer.objects.filter(pk=fresh.pk).exists())


# ── R9.07 Deactivation ────────────────────────────────────────────────────────

class CustomerDeactivateTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)
        self.customer = _make_customer()

    def test_deactivate_active_customer(self):
        url = reverse('customers:deactivate', args=[self.customer.pk])
        self.client.post(url)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)

    def test_reactivate_inactive_customer(self):
        self.customer.is_active = False
        self.customer.save()
        url = reverse('customers:deactivate', args=[self.customer.pk])
        self.client.post(url)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)

    def test_deactivate_redirects_to_detail(self):
        url = reverse('customers:deactivate', args=[self.customer.pk])
        r = self.client.post(url)
        self.assertRedirects(r, reverse('customers:detail', args=[self.customer.pk]))
