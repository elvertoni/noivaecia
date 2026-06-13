"""Tests for Sprint R12 — action permissions, audit logging, quality dashboard."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from billing.models import CashAccount, Receivable
from catalog.models import Category, Product
from company.models import Company
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()
TODAY = date.today()


def _make_company():
    Company.objects.filter(pk=1).delete()
    return Company.objects.create(name='T', last_rental_number=1, daily_interest_rate=Decimal('0'))


def _make_user(modules=(), actions=()):
    user = User.objects.create_user(email='r12@test.com', password='pass')
    for m in modules:
        ModulePermission.objects.create(user=user, module_key=m, allowed=True)
    for a in actions:
        ActionPermission.objects.create(user=user, action_key=a, allowed=True)
    return user


def _make_superuser():
    return User.objects.create_superuser(email='super@test.com', password='pass')


def _make_customer():
    return Customer.objects.create(name='Cliente Teste', city='Recife')


def _make_rental(customer=None, status='pending'):
    if not customer:
        customer = _make_customer()
    return Rental.objects.create(
        number=999, customer=customer,
        pickup_date=TODAY, return_date=TODAY + timedelta(days=7),
        total_value=Decimal('100'), status=status,
    )


# ── R12.01 Action permission matrix ─────────────────────────────────────────

class UserActionPermissionsViewTests(TestCase):
    def setUp(self):
        self.superuser = _make_superuser()
        self.client.force_login(self.superuser)
        self.target = User.objects.create_user(email='target@test.com', password='pass')

    def test_get_renders_200(self):
        r = self.client.get(reverse('user_action_permissions', kwargs={'pk': self.target.pk}))
        self.assertEqual(r.status_code, 200)

    def test_actions_in_context(self):
        r = self.client.get(reverse('user_action_permissions', kwargs={'pk': self.target.pk}))
        self.assertIn('actions', r.context)
        self.assertGreater(len(r.context['actions']), 0)

    def test_post_saves_action_permission(self):
        url = reverse('user_action_permissions', kwargs={'pk': self.target.pk})
        self.client.post(url, {'actions': ['customers.delete']})
        self.assertTrue(
            ActionPermission.objects.filter(
                user=self.target, action_key='customers.delete', allowed=True
            ).exists()
        )

    def test_post_unsets_unselected_actions(self):
        # Give the target billing.receive first
        ActionPermission.objects.create(user=self.target, action_key='billing.receive', allowed=True)
        url = reverse('user_action_permissions', kwargs={'pk': self.target.pk})
        # Post with only customers.delete — billing.receive should be set to allowed=False
        self.client.post(url, {'actions': ['customers.delete']})
        perm = ActionPermission.objects.get(user=self.target, action_key='billing.receive')
        self.assertFalse(perm.allowed)


# ── R12.02 Delete permissions ─────────────────────────────────────────────────

class CustomerDeleteActionPermissionTests(TestCase):
    def setUp(self):
        _make_company()

    def test_post_without_action_returns_403(self):
        user = _make_user(modules=['customers'])
        self.client.force_login(user)
        customer = _make_customer()
        url = reverse('customers:delete', kwargs={'pk': customer.pk})
        r = self.client.post(url)
        self.assertEqual(r.status_code, 403)

    def test_superuser_can_delete(self):
        super_user = _make_superuser()
        self.client.force_login(super_user)
        customer = _make_customer()
        url = reverse('customers:delete', kwargs={'pk': customer.pk})
        r = self.client.post(url)
        # Should redirect (success) not 403
        self.assertNotEqual(r.status_code, 403)

    def test_user_with_action_can_delete(self):
        user = _make_user(modules=['customers'], actions=['customers.delete'])
        self.client.force_login(user)
        customer = _make_customer()
        url = reverse('customers:delete', kwargs={'pk': customer.pk})
        r = self.client.post(url)
        self.assertNotEqual(r.status_code, 403)


# ── R12.05 Cancel permission ──────────────────────────────────────────────────

class RentalCancelActionPermissionTests(TestCase):
    def setUp(self):
        _make_company()

    def test_post_without_action_returns_403(self):
        user = _make_user(modules=['rentals'])
        self.client.force_login(user)
        rental = _make_rental()
        url = reverse('rentals:cancel', kwargs={'pk': rental.pk})
        r = self.client.post(url, {'reason': 'teste'})
        self.assertEqual(r.status_code, 403)

    def test_superuser_can_cancel(self):
        super_user = _make_superuser()
        self.client.force_login(super_user)
        rental = _make_rental()
        url = reverse('rentals:cancel', kwargs={'pk': rental.pk})
        r = self.client.post(url, {'reason': 'cancelamento teste'})
        self.assertNotEqual(r.status_code, 403)


# ── R12.08 Audit logging ──────────────────────────────────────────────────────

class RentalCancelAuditLogTests(TestCase):
    def setUp(self):
        _make_company()
        self.superuser = _make_superuser()
        self.client.force_login(self.superuser)

    def test_cancel_creates_audit_log(self):
        rental = _make_rental()
        url = reverse('rentals:cancel', kwargs={'pk': rental.pk})
        before_count = AuditLog.objects.filter(action='rental_cancel').count()
        self.client.post(url, {'reason': 'motivo de cancelamento'})
        after_count = AuditLog.objects.filter(action='rental_cancel').count()
        self.assertGreater(after_count, before_count)


# ── R12.06 Report export permission ───────────────────────────────────────────

class ReportExportPermissionTests(TestCase):
    def setUp(self):
        _make_company()

    def test_csv_export_without_action_returns_403(self):
        user = _make_user(modules=['reports'])
        self.client.force_login(user)
        url = reverse('reports:a_retirar')
        r = self.client.get(url, {'format': 'csv'})
        self.assertEqual(r.status_code, 403)

    def test_csv_export_with_action_returns_200(self):
        user = _make_user(modules=['reports'], actions=['reports.export'])
        self.client.force_login(user)
        url = reverse('reports:a_retirar')
        r = self.client.get(url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)

    def test_superuser_can_export(self):
        super_user = _make_superuser()
        self.client.force_login(super_user)
        url = reverse('reports:a_retirar')
        r = self.client.get(url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)


# ── R12.09 Import quality dashboard ───────────────────────────────────────────

class ImportQualityViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user(modules=['maintenance'])
        self.client.force_login(self.user)

    def test_200_renders(self):
        r = self.client.get(reverse('maintenance:import_quality'))
        self.assertEqual(r.status_code, 200)

    def test_placeholder_count_in_context(self):
        Category.objects.create(prefix='TST', name='Teste Placeholder', is_placeholder=True)
        r = self.client.get(reverse('maintenance:import_quality'))
        self.assertGreaterEqual(r.context['placeholder_categories'], 1)

    def test_duplicate_pairs_in_context(self):
        cat = Category.objects.create(prefix='DUP', name='Dup')
        Product.objects.create(category=cat, code=1, description='A', value=Decimal('10'))
        Product.objects.create(category=cat, code=1, description='B', value=Decimal('10'))
        r = self.client.get(reverse('maintenance:import_quality'))
        self.assertGreaterEqual(r.context['duplicate_product_pairs'], 1)


# ── R12.07 Legacy audit view ──────────────────────────────────────────────────

class LegacyAuditViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user(modules=['maintenance'])
        self.client.force_login(self.user)

    def test_200_renders(self):
        r = self.client.get(reverse('maintenance:legacy_audit'))
        self.assertEqual(r.status_code, 200)

    def test_programas_and_libera_in_context(self):
        r = self.client.get(reverse('maintenance:legacy_audit'))
        self.assertIn('programas', r.context)
        self.assertIn('libera', r.context)
