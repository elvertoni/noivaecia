from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from catalog.availability import find_overlapping_rental, find_rental_for
from catalog.forms import CategoryForm, ProductForm
from catalog.models import Category, Product
from customers.models import Customer
from rentals.models import Rental, RentalItem

User = get_user_model()


class CatalogModelTests(TestCase):
    def test_category_str(self):
        category = Category.objects.create(prefix='VN', name='Vestidos')
        self.assertEqual(str(category), 'VN · Vestidos')

    def test_product_allows_legacy_duplicate_codes(self):
        category = Category.objects.create(prefix='VN', name='Vestidos')
        Product.objects.create(category=category, code=1, description='A', value=10)
        Product.objects.create(category=category, code=1, description='B', value=20)
        self.assertEqual(Product.objects.filter(category=category, code=1).count(), 2)


class CatalogFormValidationTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(prefix='VES', name='Vestidos')

    def test_category_prefix_is_canonical_and_case_insensitively_unique(self):
        duplicate = CategoryForm(data={'prefix': ' ves ', 'name': 'Outra'})
        self.assertFalse(duplicate.is_valid())
        self.assertIn('Já existe uma categoria', duplicate.errors['prefix'][0])

        form = CategoryForm(data={'prefix': 'trn', 'name': 'Ternos'})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['prefix'], 'TRN')

    def test_product_value_cannot_be_negative(self):
        form = ProductForm(data={
            'category': self.category.pk,
            'code': 1,
            'description': 'Vestido',
            'color': '',
            'size': '',
            'value': '-1,00',
            'notes': '',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('value', form.errors)


class AvailabilityTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.category = Category.objects.create(prefix='VN', name='Vestidos')
        self.product = Product.objects.create(category=self.category, code=1, description='A', value=300)
        self.rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 20),
            status=Rental.Status.PICKED_UP,
        )
        RentalItem.objects.create(rental=self.rental, product=self.product, value=300)

    def test_rented_inside_window(self):
        self.assertEqual(find_rental_for(self.product, date(2026, 6, 15)), self.rental)

    def test_available_outside_window(self):
        self.assertIsNone(find_rental_for(self.product, date(2026, 6, 25)))

    def test_available_after_returned(self):
        self.rental.status = Rental.Status.RETURNED
        self.rental.save()
        self.assertIsNone(find_rental_for(self.product, date(2026, 6, 15)))

    def test_available_after_cancelled(self):
        # A cancelled rental no longer holds its items.
        self.rental.status = Rental.Status.CANCELLED
        self.rental.save()
        self.assertIsNone(find_rental_for(self.product, date(2026, 6, 15)))

    def test_overlap_finds_conflict_after_pickup_date(self):
        self.assertEqual(
            find_overlapping_rental(self.product, date(2026, 6, 1), date(2026, 6, 12)),
            self.rental,
        )


class ProductBrowseViewTests(TestCase):
    """Faceted product picker endpoint (rental item modal)."""

    @classmethod
    def setUpTestData(cls):
        cls.url = reverse('catalog:product_browse')
        cls.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        # The picker serves the rental form, so rentals access is enough.
        ModulePermission.objects.create(user=cls.user, module_key='rentals', allowed=True)
        cls.customer = Customer.objects.create(name='Maria')

        cls.blazers = Category.objects.create(prefix='BMA', name='Blazer masculino')
        cls.dresses = Category.objects.create(prefix='VF', name='Vestidos de festa')

        # Blazers: two sizes, two colors.
        cls.b54_cinza = Product.objects.create(
            category=cls.blazers, code=500, description='Bleizer italiano', color='CINZA', size='54', value=120
        )
        cls.b54_preto = Product.objects.create(
            category=cls.blazers, code=501, description='Bleizer italiano', color='PRETO', size='54', value=120
        )
        cls.b50_cinza = Product.objects.create(
            category=cls.blazers, code=502, description='Bleizer slim', color='CINZA', size='50', value=120
        )
        # A dress, plus an empty legacy slot (blanked description).
        Product.objects.create(category=cls.dresses, code=1, description='Vestido longo', color='ROSE', size='M', value=300)
        cls.empty_slot = Product.objects.create(
            category=cls.blazers, code=999, description='', color='', size='', value=0
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, **params):
        return self.client.get(self.url, params).json()

    def test_requires_authentication(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_without_module_is_denied(self):
        outsider = User.objects.create_user(email='out@b.com', password='Senha12345')
        self.client.force_login(outsider)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_category_facet_counts(self):
        data = self._get()
        counts = {c['category__prefix']: c['n'] for c in data['categories']}
        # 3 visible blazers (empty slot hidden) + 1 dress.
        self.assertEqual(counts['BMA'], 3)
        self.assertEqual(counts['VF'], 1)

    def test_size_facet_scoped_to_category(self):
        data = self._get(prefix='BMA')
        sizes = {s['size']: s['n'] for s in data['facets']['sizes']}
        self.assertEqual(sizes, {'50': 1, '54': 2})

    def test_filter_by_prefix_and_size(self):
        data = self._get(prefix='BMA', size='54')
        codes = {r['code'] for r in data['results']}
        self.assertEqual(codes, {'BMA500', 'BMA501'})

    def test_filter_by_color(self):
        data = self._get(prefix='BMA', color='cinza')
        codes = {r['code'] for r in data['results']}
        self.assertEqual(codes, {'BMA500', 'BMA502'})

    def test_text_query_matches_code(self):
        data = self._get(q='BMA500')
        codes = {r['code'] for r in data['results']}
        self.assertIn('BMA500', codes)

    def test_empty_slot_hidden_by_default_and_shown_on_request(self):
        default_codes = {r['code'] for r in self._get(prefix='BMA')['results']}
        self.assertNotIn('BMA999', default_codes)
        shown_codes = {r['code'] for r in self._get(prefix='BMA', empty='1')['results']}
        self.assertIn('BMA999', shown_codes)

    def test_availability_inline_marks_active_rental(self):
        rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 20),
            status=Rental.Status.PICKED_UP,
        )
        RentalItem.objects.create(rental=rental, product=self.b54_cinza, value=120)
        results = {r['code']: r for r in self._get(prefix='BMA', size='54', date='2026-06-15')['results']}
        self.assertFalse(results['BMA500']['available'])
        self.assertEqual(results['BMA500']['rental']['number'], 1)
        self.assertEqual(results['BMA500']['rental']['customer'], 'Maria')
        self.assertTrue(results['BMA501']['available'])

    def test_availability_inline_accepts_brazilian_dates(self):
        rental = Rental.objects.create(
            number=4, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 20),
            status=Rental.Status.PICKED_UP,
        )
        RentalItem.objects.create(rental=rental, product=self.b54_cinza, value=120)

        results = {
            row['code']: row for row in self._get(
                prefix='BMA', date='15/06/2026',
            )['results']
        }

        self.assertFalse(results['BMA500']['available'])

    def test_availability_inline_uses_full_rental_window(self):
        rental = Rental.objects.create(
            number=3, customer=self.customer,
            pickup_date=date(2026, 6, 15), return_date=date(2026, 6, 20),
            status=Rental.Status.PICKED_UP,
        )
        RentalItem.objects.create(rental=rental, product=self.b54_cinza, value=120)
        results = {
            r['code']: r for r in self._get(
                prefix='BMA',
                size='54',
                pickup_date='2026-06-10',
                return_date='2026-06-16',
            )['results']
        }
        self.assertFalse(results['BMA500']['available'])
        self.assertEqual(results['BMA500']['rental']['number'], 3)

    def test_cancelled_rental_does_not_block(self):
        rental = Rental.objects.create(
            number=2, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 20),
            status=Rental.Status.CANCELLED,
        )
        RentalItem.objects.create(rental=rental, product=self.b54_cinza, value=120)
        results = {r['code']: r for r in self._get(prefix='BMA', size='54', date='2026-06-15')['results']}
        self.assertTrue(results['BMA500']['available'])

    def test_pagination(self):
        cat = Category.objects.create(prefix='CAM', name='Camisas')
        for i in range(30):
            Product.objects.create(category=cat, code=i + 1, description='Camisa', value=50)
        first = self._get(prefix='CAM', page=1)
        self.assertEqual(first['total'], 30)
        self.assertEqual(first['num_pages'], 2)
        self.assertEqual(len(first['results']), 24)
        second = self._get(prefix='CAM', page=2)
        self.assertEqual(len(second['results']), 6)


class ModulePermissionAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        self.client.force_login(self.user)

    def test_denied_without_permission(self):
        self.assertEqual(self.client.get('/catalogo/produtos/').status_code, 403)

    def test_allowed_with_permission(self):
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.assertEqual(self.client.get('/catalogo/produtos/').status_code, 200)

    def test_revoked_permission_denies(self):
        perm = ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.assertEqual(self.client.get('/catalogo/produtos/').status_code, 200)
        perm.allowed = False
        perm.save()
        self.assertEqual(self.client.get('/catalogo/produtos/').status_code, 403)


class ProductOverflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        ModulePermission.objects.create(user=self.user, module_key='rentals', allowed=True)
        self.client.force_login(self.user)

    def test_product_list_code_overflow_protection(self):
        # A code value that overflows 32-bit signed integer (9999999999)
        response = self.client.get('/catalogo/produtos/?code=9999999999')
        self.assertEqual(response.status_code, 200)

    def test_product_availability_json_overflow_protection(self):
        # product_id value that overflows 32-bit signed integer (9999999999)
        url = reverse('catalog:availability_json')
        response = self.client.get(f'{url}?product_id=9999999999&date=2026-06-18')
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'available': False, 'error': 'not_found'})


class ProductAvailabilityJsonTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='avail@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='rentals', allowed=True)
        self.client.force_login(self.user)
        self.url = reverse('catalog:availability_json')
        self.customer = Customer.objects.create(name='Maria')
        self.category = Category.objects.create(prefix='VN', name='Vestidos')
        self.product = Product.objects.create(
            category=self.category, code=20, description='Vestido renda', value=300
        )

    def test_uses_full_rental_window_for_partial_overlap(self):
        rental = Rental.objects.create(
            number=20,
            customer=self.customer,
            pickup_date=date(2026, 6, 15),
            return_date=date(2026, 6, 20),
            status=Rental.Status.PICKED_UP,
        )
        RentalItem.objects.create(rental=rental, product=self.product, value=300)

        response = self.client.get(self.url, {
            'product_id': self.product.pk,
            'pickup_date': '2026-06-10',
            'return_date': '2026-06-16',
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['available'])
        self.assertEqual(data['rental_number'], 20)
        self.assertEqual(data['pickup_date'], '2026-06-15')
        self.assertEqual(data['return_date'], '2026-06-20')

    def test_accepts_brazilian_dates(self):
        rental = Rental.objects.create(
            number=21,
            customer=self.customer,
            pickup_date=date(2026, 6, 15),
            return_date=date(2026, 6, 20),
            status=Rental.Status.PICKED_UP,
        )
        RentalItem.objects.create(rental=rental, product=self.product, value=300)

        response = self.client.get(self.url, {
            'product_id': self.product.pk,
            'pickup_date': '10/06/2026',
            'return_date': '16/06/2026',
        })

        self.assertFalse(response.json()['available'])

    def test_rejects_invalid_dates_without_claiming_availability(self):
        response = self.client.get(self.url, {
            'product_id': self.product.pk,
            'pickup_date': '31/02/2026',
            'return_date': '01/03/2026',
        })

        self.assertJSONEqual(response.content, {'available': False, 'error': 'invalid_date'})
