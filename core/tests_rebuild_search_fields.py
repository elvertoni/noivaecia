from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from catalog.models import Category, Product
from customers.models import Customer


class RebuildSearchFieldsCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = Category.objects.create(prefix='V', name='Veus')
        Customer.objects.bulk_create(
            [
                Customer(
                    name='  Jo\u00e3o   D\'Avila  ',
                    cpf='123.456.789-00',
                    rg='12.345.678-9',
                    phone_home='(43) 3333-4444',
                    phone_mobile='(43) 99999-8888',
                    phone_work='43 3555-6666',
                    cpf_digits='',
                    rg_digits='',
                    phone_home_digits='',
                    phone_mobile_digits='',
                    phone_work_digits='',
                    name_search='',
                ),
                Customer(
                    name='Maria de F\u00e1tima',
                    cpf='987.654.321-00',
                    phone_mobile='(11) 98888-7777',
                    cpf_digits='',
                    phone_mobile_digits='',
                    name_search='',
                ),
            ]
        )
        Product.objects.bulk_create(
            [
                Product(
                    category=category,
                    code=1,
                    description='  V\u00e9u   Cl\u00e1ssico  ',
                    description_search='',
                ),
                Product(
                    category=category,
                    code=2,
                    description='Saia Rendada',
                    description_search='',
                ),
            ]
        )

    def run_command(self):
        output = StringIO()
        call_command('rebuild_search_fields', batch_size=1, stdout=output)
        return output.getvalue()

    def test_rebuilds_normalized_fields_created_empty_by_bulk_create(self):
        output = self.run_command()

        joao = Customer.objects.get(cpf='123.456.789-00')
        self.assertEqual(joao.cpf_digits, '12345678900')
        self.assertEqual(joao.rg_digits, '123456789')
        self.assertEqual(joao.phone_home_digits, '4333334444')
        self.assertEqual(joao.phone_mobile_digits, '43999998888')
        self.assertEqual(joao.phone_work_digits, '4335556666')
        self.assertEqual(joao.name_search, "joao d'avila")

        maria = Customer.objects.get(cpf='987.654.321-00')
        self.assertEqual(maria.cpf_digits, '98765432100')
        self.assertEqual(maria.phone_mobile_digits, '11988887777')
        self.assertEqual(maria.name_search, 'maria de fatima')

        products = dict(Product.objects.values_list('code', 'description_search'))
        self.assertEqual(products, {1: 'veu classico', 2: 'saia rendada'})
        self.assertIn('2 cliente(s) e 2 produto(s) atualizados', output)

    def test_second_execution_is_idempotent_and_does_not_update_rows(self):
        self.run_command()

        with CaptureQueriesContext(connection) as queries:
            output = self.run_command()

        update_queries = [
            query['sql']
            for query in queries.captured_queries
            if query['sql'].lstrip().upper().startswith('UPDATE')
        ]
        self.assertEqual(update_queries, [])
        self.assertIn('0 cliente(s) e 0 produto(s) atualizados', output)

    def test_rejects_non_positive_batch_size(self):
        with self.assertRaisesMessage(CommandError, 'maior que zero'):
            call_command('rebuild_search_fields', batch_size=0)
