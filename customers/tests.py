from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from customers.forms import CustomerForm
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

    def test_alternate_phone_contact_defaults_to_empty_for_existing_data(self):
        customer = Customer.objects.create(name='Cliente legado')

        self.assertEqual(customer.alternate_phone_contact, '')


class CustomerCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='customers', allowed=True)
        self.client.force_login(self.user)

    def test_create_customer(self):
        response = self.client.post('/clientes/novo/', {
            'name': 'Maria', 'address': '', 'district': '', 'state': 'PR', 'city': 'Recife',
            'rg': '', 'cpf': '', 'phone_home': '', 'phone_mobile': '', 'phone_work': '', 'notes': '',
        })
        self.assertEqual(response.status_code, 302)
        customer = Customer.objects.get(name='Maria')
        self.assertEqual(customer.alternate_phone_contact, '')

    def test_create_customer_preserves_rg_and_alternate_phone_contact(self):
        response = self.client.post(reverse('customers:create'), {
            'name': 'Maria',
            'address': '',
            'district': '',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '8.241.995-0',
            'cpf': '',
            'phone_mobile': '(43) 99123-4567',
            'phone_home': '(43) 98888-7777',
            'alternate_phone_contact': 'Esposo João',
            'phone_work': '',
            'notes': '',
        })

        self.assertRedirects(response, reverse('customers:list'))
        customer = Customer.objects.get(name='Maria')
        self.assertEqual(customer.rg, '8.241.995-0')
        self.assertEqual(customer.rg_digits, '82419950')
        self.assertEqual(customer.alternate_phone_contact, 'Esposo João')

    def test_update_customer_changes_alternate_phone_identification(self):
        customer = Customer.objects.create(
            name='Maria',
            rg='8.241.995-0',
            phone_home='(43) 98888-7777',
        )

        response = self.client.post(reverse('customers:update', args=[customer.pk]), {
            'name': 'Maria',
            'address': '',
            'district': '',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '12.345.678-X',
            'cpf': '',
            'phone_mobile': '',
            'phone_home': '(43) 98888-7777',
            'alternate_phone_contact': 'Mãe da cliente',
            'phone_work': '',
            'notes': '',
        })

        self.assertRedirects(response, reverse('customers:list'))
        customer.refresh_from_db()
        self.assertEqual(customer.rg, '12.345.678-X')
        self.assertEqual(customer.alternate_phone_contact, 'Mãe da cliente')

        detail = self.client.get(reverse('customers:detail', args=[customer.pk]))
        self.assertContains(detail, '(43) 98888-7777')
        self.assertContains(detail, 'Mãe da cliente')

    def test_search_by_name(self):
        Customer.objects.create(name='Ana Souza')
        Customer.objects.create(name='Carlos Lima')
        response = self.client.get('/clientes/?q=Ana')
        self.assertContains(response, 'Ana Souza')
        self.assertNotContains(response, 'Carlos Lima')

    def test_search_by_alternate_phone_number_and_contact(self):
        customer = Customer.objects.create(
            name='Ana Souza',
            phone_home='(43) 98888-7777',
            alternate_phone_contact='Esposo João',
        )

        by_number = self.client.get('/clientes/', {'q': '43988887777'})
        by_contact = self.client.get('/clientes/', {'q': 'Esposo João'})
        quick_search = self.client.get(
            reverse('customers:search'),
            {'q': '43988887777'},
        )

        self.assertContains(by_number, customer.name)
        self.assertContains(by_contact, customer.name)
        self.assertEqual(quick_search.json()['results'][0]['id'], customer.pk)

    def test_search_large_integer_overflow_protection(self):
        # A 10-digit number that overflows a 32-bit signed integer (e.g. 9999999999)
        # Should not crash the server and return a successful empty list
        response = self.client.get('/clientes/?q=9999999999')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Ana Souza')

    def test_create_form_keeps_city_entry_available_without_javascript(self):
        response = self.client.get('/clientes/novo/')

        self.assertContains(response, 'list="customer-city-options"')
        self.assertContains(response, '<datalist id="customer-city-options">', html=False)

    def test_rg_field_has_no_fixed_javascript_mask(self):
        response = self.client.get(reverse('customers:create'))

        self.assertContains(response, 'placeholder="Ex.: 8.241.995-0"')
        self.assertContains(response, 'letras e pontuação serão preservadas')
        self.assertNotContains(response, 'data-mask="rg"')
        self.assertContains(response, 'data-rg-format="true"')


class CustomerFormNormalizationTests(TestCase):
    def test_preserves_real_rg_format_and_updates_digit_lookup(self):
        form = CustomerForm(data={
            'name': 'Maria Silva',
            'address': '',
            'district': '',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '8.241.995-0',
            'cpf': '',
            'phone_home': '',
            'alternate_phone_contact': '',
            'phone_mobile': '',
            'phone_work': '',
            'notes': '',
        })

        self.assertTrue(form.is_valid(), form.errors)
        customer = form.save()
        self.assertEqual(customer.rg, '8.241.995-0')
        self.assertEqual(customer.rg_digits, '82419950')

    def test_formats_unpunctuated_numeric_rg_without_assuming_other_lengths(self):
        eight_digits = CustomerForm(data={
            'name': 'Maria Silva',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '82419950',
        })
        nine_digits = CustomerForm(data={
            'name': 'Joana Silva',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '123456789',
        })
        other_length = CustomerForm(data={
            'name': 'Clara Silva',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '1234567',
        })

        self.assertTrue(eight_digits.is_valid(), eight_digits.errors)
        self.assertTrue(nine_digits.is_valid(), nine_digits.errors)
        self.assertTrue(other_length.is_valid(), other_length.errors)
        self.assertEqual(eight_digits.cleaned_data['rg'], '8.241.995-0')
        self.assertEqual(nine_digits.cleaned_data['rg'], '12.345.678-9')
        self.assertEqual(other_length.cleaned_data['rg'], '1234567')

    def test_accepts_and_preserves_alphanumeric_rg_check_digit(self):
        form = CustomerForm(data={
            'name': 'Maria Silva',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '12.345.678-X',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['rg'], '12.345.678-X')

    def test_rg_rejects_unsupported_characters(self):
        form = CustomerForm(data={
            'name': 'Maria Silva',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '8.241.995@0',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('rg', form.errors)

    def test_accepts_phone_with_brazilian_country_code_and_saves_local_format(self):
        form = CustomerForm(data={
            'name': 'Maria Silva',
            'address': '',
            'district': '',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '',
            'cpf': '',
            'phone_home': '',
            'phone_mobile': '+55 (43) 99999-8888',
            'phone_work': '',
            'notes': '',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone_mobile'], '(43) 99999-8888')

    def test_invalid_cpf_returns_a_field_error(self):
        form = CustomerForm(data={
            'name': 'Maria Silva',
            'address': '',
            'district': '',
            'state': 'PR',
            'city': 'Bandeirantes',
            'rg': '',
            'cpf': '111.111.111-11',
            'phone_home': '',
            'phone_mobile': '',
            'phone_work': '',
            'notes': '',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('cpf', form.errors)


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
