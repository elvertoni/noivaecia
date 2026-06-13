from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import ModulePermission
from customers.models import Customer

User = get_user_model()


class CustomerModelTests(TestCase):
    def test_str_is_name(self):
        customer = Customer.objects.create(name='Maria Silva')
        self.assertEqual(str(customer), 'Maria Silva')

    def test_timestamps_present(self):
        customer = Customer.objects.create(name='Maria')
        self.assertIsNotNone(customer.created_at)
        self.assertIsNotNone(customer.updated_at)


class CustomerCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='customers', allowed=True)
        self.client.force_login(self.user)

    def test_create_customer(self):
        response = self.client.post('/clientes/novo/', {
            'name': 'Maria', 'address': '', 'district': '', 'city': 'Recife',
            'rg': '', 'cpf': '123', 'phone_home': '', 'phone_mobile': '', 'phone_work': '', 'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Customer.objects.filter(name='Maria').exists())

    def test_search_by_name(self):
        Customer.objects.create(name='Ana Souza')
        Customer.objects.create(name='Carlos Lima')
        response = self.client.get('/clientes/?q=Ana')
        self.assertContains(response, 'Ana Souza')
        self.assertNotContains(response, 'Carlos Lima')


class CustomerLegacyFieldTests(TestCase):
    """R3.01, R3.02 — legacy metadata and placeholder flag on Customer."""

    def test_legacy_fields_default_empty(self):
        c = Customer.objects.create(name='Normal')
        self.assertIsNone(c.legacy_id)
        self.assertEqual(c.legacy_source, '')
        self.assertEqual(c.legacy_notes, '')
        self.assertFalse(c.is_placeholder)

    def test_legacy_fields_store_correctly(self):
        c = Customer.objects.create(
            name='PLACEHOLDER',
            legacy_id=99, legacy_source='clientes',
            legacy_notes='numero ausente no legado',
            is_placeholder=True,
        )
        c.refresh_from_db()
        self.assertEqual(c.legacy_id, 99)
        self.assertTrue(c.is_placeholder)

    def test_placeholder_filter(self):
        Customer.objects.create(name='Real')
        Customer.objects.create(name='Ghost', is_placeholder=True)
        self.assertEqual(Customer.objects.filter(is_placeholder=True).count(), 1)
        self.assertEqual(Customer.objects.filter(is_placeholder=False).count(), 1)
