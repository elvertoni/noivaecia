from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import ModulePermission
from core.models import AuditLog
from customers.models import Customer

User = get_user_model()


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
