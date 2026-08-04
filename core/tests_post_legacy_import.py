from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer


class PostLegacyImportCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = Category.objects.create(prefix='V', name='Vestidos')
        Customer.objects.bulk_create(
            [
                Customer(
                    name='João da Silva',
                    city='BTES',
                    cpf='123.456.789-00',
                    cpf_digits='',
                    name_search='',
                ),
                Customer(
                    name='Maria de Fátima',
                    city='Andirá',
                    phone_mobile='(43) 99999-8888',
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
                    description='Vestido Clássico',
                    description_search='',
                    value=Decimal('250.00'),
                ),
                Product(
                    category=category,
                    code=2,
                    description='Véu',
                    description_search='',
                    value=Decimal('0.00'),
                ),
            ]
        )
        Company.objects.create(name='NOIVAS & CIA', last_rental_number=56526)

    def run_command(self, **options):
        output = StringIO()
        call_command('post_legacy_import', batch_size=1, stdout=output, **options)
        return output.getvalue()

    def test_dry_run_reports_counts_without_writes(self):
        with CaptureQueriesContext(connection) as queries:
            output = self.run_command(dry_run=True)

        update_queries = [
            query['sql']
            for query in queries.captured_queries
            if query['sql'].lstrip().upper().startswith('UPDATE')
        ]
        self.assertEqual(update_queries, [])
        self.assertEqual(Customer.objects.get(name='João da Silva').city, 'BTES')
        self.assertEqual(Customer.objects.get(name='João da Silva').cpf_digits, '')
        self.assertEqual(Product.objects.get(code=1).description_search, '')
        self.assertEqual(Product.objects.get(code=1).value, Decimal('250.00'))
        self.assertIn('Modo: SIMULACAO', output)
        self.assertIn('Cidades a normalizar: 1', output)
        self.assertIn('Clientes com busca a reconstruir: 2', output)
        self.assertIn('Produtos com busca a reconstruir: 2', output)
        self.assertIn('Produtos com valor a zerar: 1', output)

    def test_applies_all_steps_and_second_execution_is_idempotent(self):
        company_before = Company.objects.values().get(pk=1)
        output = self.run_command(apply=True)

        joao = Customer.objects.get(name='João da Silva')
        maria = Customer.objects.get(name='Maria de Fátima')
        self.assertEqual(joao.city, 'Bandeirantes')
        self.assertEqual(joao.cpf_digits, '12345678900')
        self.assertEqual(joao.name_search, 'joao da silva')
        self.assertEqual(maria.phone_mobile_digits, '43999998888')
        self.assertEqual(maria.name_search, 'maria de fatima')
        self.assertEqual(Product.objects.get(code=1).description_search, 'vestido classico')
        self.assertEqual(Product.objects.get(code=2).description_search, 'veu')
        self.assertFalse(Product.objects.filter(value__gt=0).exists())
        self.assertEqual(Company.objects.values().get(pk=1), company_before)
        self.assertIn('Modo: APLICADO', output)

        with CaptureQueriesContext(connection) as queries:
            second_output = self.run_command(apply=True)

        update_queries = [
            query['sql']
            for query in queries.captured_queries
            if query['sql'].lstrip().upper().startswith('UPDATE')
        ]
        self.assertEqual(update_queries, [])
        self.assertIn('Cidades a normalizar: 0', second_output)
        self.assertIn('Clientes com busca a reconstruir: 0', second_output)
        self.assertIn('Produtos com busca a reconstruir: 0', second_output)
        self.assertIn('Produtos com valor a zerar: 0', second_output)

    def test_preview_is_the_default_mode(self):
        output = self.run_command()

        self.assertEqual(Customer.objects.get(name='João da Silva').city, 'BTES')
        self.assertEqual(Product.objects.get(code=1).value, Decimal('250.00'))
        self.assertIn('Modo: SIMULACAO', output)

    @mock.patch(
        'core.management.commands.post_legacy_import._rebuild_product_search',
        side_effect=RuntimeError('falha simulada'),
    )
    def test_rolls_back_every_change_when_a_step_fails(self, _rebuild):
        with self.assertRaisesRegex(RuntimeError, 'falha simulada'):
            self.run_command(apply=True)

        customer = Customer.objects.get(name='João da Silva')
        product = Product.objects.get(code=1)
        self.assertEqual(customer.city, 'BTES')
        self.assertEqual(customer.cpf_digits, '')
        self.assertEqual(customer.name_search, '')
        self.assertEqual(product.description_search, '')
        self.assertEqual(product.value, Decimal('250.00'))
