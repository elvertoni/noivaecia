from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()


class HealthcheckTests(TestCase):
    def test_healthz_returns_ok(self):
        response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})


class DashboardModuleTests(TestCase):
    def test_dashboard_only_lists_allowed_modules(self):
        user = User.objects.create_user(email='ops@b.com', password='Senha12345')
        ModulePermission.objects.create(
            user=user,
            module_key='customers',
            allowed=True,
        )
        self.client.force_login(user)

        response = self.client.get('/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Clientes')
        self.assertNotContains(response, 'Manutenção')
        self.assertEqual(response.context['indicators'], [])
        self.assertNotContains(response, 'Recebimentos em aberto')

    def test_dashboard_lists_maintenance_when_allowed(self):
        user = User.objects.create_user(email='maint@b.com', password='Senha12345')
        ModulePermission.objects.create(
            user=user,
            module_key='maintenance',
            allowed=True,
        )
        self.client.force_login(user)

        response = self.client.get('/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Manutenção')

    def test_module_shortcuts_point_to_their_own_apps(self):
        user = User.objects.create_user(email='links@b.com', password='Senha12345')
        ModulePermission.objects.create(
            user=user,
            module_key='movements',
            allowed=True,
        )
        ModulePermission.objects.create(
            user=user,
            module_key='billing',
            allowed=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))
        module_urls = {
            module['key']: module['url']
            for module in response.context['modules']
        }

        self.assertEqual(module_urls['movements'], reverse('movements:pickup_list'))
        self.assertEqual(module_urls['billing'], reverse('billing:dashboard'))

    def test_dashboard_pickup_indicator_excludes_legacy_payment_only_records(self):
        user = User.objects.create_user(email='pickup@b.com', password='Senha12345')
        ModulePermission.objects.create(user=user, module_key='movements', allowed=True)
        customer = Customer.objects.create(name='Cliente da fila')
        Rental.objects.create(
            number=1,
            customer=customer,
            pickup_date='2026-08-03',
            return_date='2026-08-10',
        )
        Rental.objects.create(
            number=2,
            customer=customer,
            pickup_date='2010-01-01',
            return_date='2010-01-08',
            legacy_notes=Rental.LEGACY_PAGAR_ONLY_MARKER,
        )
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))

        indicators = {indicator['label']: indicator['value'] for indicator in response.context['indicators']}
        self.assertEqual(indicators['Locações a retirar'], 1)


class AuditLogTests(TestCase):
    """R3.10 — sensitive action audit log."""

    def setUp(self):
        self.user = User.objects.create_user(email='op@b.com', password='Senha12345')

    def test_record_factory_creates_entry(self):
        customer = Customer.objects.create(name='Maria')
        log = AuditLog.record(
            user=self.user,
            action='delete',
            obj=customer,
            reason='Teste',
        )
        self.assertEqual(log.action, 'delete')
        self.assertEqual(log.model_name, 'Customer')
        self.assertEqual(log.object_id, str(customer.pk))
        self.assertEqual(log.reason, 'Teste')
        self.assertIsNotNone(log.created_at)

    def test_record_without_user(self):
        customer = Customer.objects.create(name='Sistema')
        log = AuditLog.record(user=None, action='import', obj=customer)
        self.assertIsNone(log.user)

    def test_record_with_metadata(self):
        customer = Customer.objects.create(name='Ana')
        log = AuditLog.record(
            user=self.user,
            action='cancel',
            obj=customer,
            metadata={'reason': 'Desistência', 'rental_id': 42},
        )
        self.assertEqual(log.metadata['rental_id'], 42)

    def test_str_representation(self):
        customer = Customer.objects.create(name='Rita')
        log = AuditLog.record(user=self.user, action='payment', obj=customer)
        self.assertIn('payment', str(log))
        self.assertIn('Customer', str(log))
