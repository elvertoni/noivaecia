"""Tests for Sprint R8 — catalog filters, disambiguation, history, placeholder review, merge."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer
from rentals.models import Rental, RentalItem

User = get_user_model()


def _make_catalog():
    """Create two categories and four products (two with duplicate prefix+code)."""
    cat_a = Category.objects.create(prefix='VES', name='Vestidos')
    cat_b = Category.objects.create(prefix='TRN', name='Ternos', is_placeholder=True)
    p1 = Product.objects.create(category=cat_a, code=1, description='Vestido branco', color='branco', size='M', value=Decimal('100'))
    p2 = Product.objects.create(category=cat_a, code=1, description='Vestido off-white', color='off-white', size='G', value=Decimal('120'))
    p3 = Product.objects.create(category=cat_a, code=2, description='Vestido azul', color='azul', size='P', value=Decimal('90'))
    p4 = Product.objects.create(category=cat_b, code=10, description='Terno preto', color='preto', size='44', value=Decimal('150'), is_placeholder=True)
    return cat_a, cat_b, p1, p2, p3, p4


def _make_user(module_key='catalog'):
    user = User.objects.create_user(email='cat@test.com', password='pass')
    ModulePermission.objects.create(user=user, module_key=module_key, allowed=True)
    return user


def _make_rental_with_item(product, customer=None):
    Company.objects.filter(pk=1).delete()
    Company.objects.create(name='T', last_rental_number=1)
    if customer is None:
        customer = Customer.objects.create(name='Cliente Teste', city='Recife')
    rental = Rental.objects.create(
        number=500, customer=customer,
        pickup_date=date(2026, 6, 1), return_date=date(2026, 6, 10),
        total_value=Decimal('100'),
    )
    RentalItem.objects.create(rental=rental, product=product, value=Decimal('100'))
    return rental


# ── R8.01 Product list filters ────────────────────────────────────────────────

class ProductListFiltersTests(TestCase):
    def setUp(self):
        self.cat_a, self.cat_b, self.p1, self.p2, self.p3, self.p4 = _make_catalog()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('catalog:product_list')

    def test_no_filters_returns_all(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['products'].count(), 4)

    def test_filter_by_prefix(self):
        response = self.client.get(self.url, {'prefix': 'VES'})
        codes = {p.pk for p in response.context['products']}
        self.assertIn(self.p1.pk, codes)
        self.assertNotIn(self.p4.pk, codes)

    def test_filter_by_code(self):
        response = self.client.get(self.url, {'code': '1'})
        pks = {p.pk for p in response.context['products']}
        self.assertIn(self.p1.pk, pks)
        self.assertIn(self.p2.pk, pks)
        self.assertNotIn(self.p3.pk, pks)

    def test_filter_by_description(self):
        response = self.client.get(self.url, {'description': 'azul'})
        pks = {p.pk for p in response.context['products']}
        self.assertEqual(pks, {self.p3.pk})

    def test_filter_by_color(self):
        response = self.client.get(self.url, {'color': 'branco'})
        pks = {p.pk for p in response.context['products']}
        self.assertEqual(pks, {self.p1.pk})

    def test_filter_by_size(self):
        response = self.client.get(self.url, {'size': 'G'})
        pks = {p.pk for p in response.context['products']}
        self.assertEqual(pks, {self.p2.pk})

    def test_filter_placeholder_only(self):
        response = self.client.get(self.url, {'placeholder': '1'})
        pks = {p.pk for p in response.context['products']}
        self.assertEqual(pks, {self.p4.pk})

    def test_filter_duplicate_only(self):
        response = self.client.get(self.url, {'duplicate': '1'})
        pks = {p.pk for p in response.context['products']}
        # p1 and p2 share (VES, 1)
        self.assertIn(self.p1.pk, pks)
        self.assertIn(self.p2.pk, pks)
        self.assertNotIn(self.p3.pk, pks)
        self.assertNotIn(self.p4.pk, pks)


# ── R8.02 Badges ──────────────────────────────────────────────────────────────

class ProductListBadgesTests(TestCase):
    def setUp(self):
        _make_catalog()
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_duplicate_ids_in_context(self):
        response = self.client.get(reverse('catalog:product_list'))
        dup_ids = response.context['duplicate_ids']
        cat_a = Category.objects.get(prefix='VES')
        p1 = Product.objects.get(category=cat_a, code=1, color='branco')
        p2 = Product.objects.get(category=cat_a, code=1, color='off-white')
        self.assertIn(p1.pk, dup_ids)
        self.assertIn(p2.pk, dup_ids)

    def test_placeholder_count_in_context(self):
        response = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(response.context['placeholder_count'], 1)

    def test_category_list_shows_placeholder_count(self):
        response = self.client.get(reverse('catalog:category_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['placeholder_count'], 1)


# ── R8.03 Availability disambiguation ────────────────────────────────────────

class AvailabilityDisambiguationTests(TestCase):
    def setUp(self):
        self.cat_a, self.cat_b, self.p1, self.p2, self.p3, self.p4 = _make_catalog()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('catalog:availability')

    def test_single_product_no_disambiguation(self):
        response = self.client.get(self.url, {'prefix': 'VES', 'code': '2', 'date': '2026-06-15'})
        self.assertFalse(response.context.get('needs_disambiguation'))
        self.assertEqual(response.context['product'], self.p3)

    def test_duplicate_triggers_disambiguation(self):
        response = self.client.get(self.url, {'prefix': 'VES', 'code': '1', 'date': '2026-06-15'})
        self.assertTrue(response.context.get('needs_disambiguation'))
        self.assertEqual(len(response.context['candidates']), 2)

    def test_product_id_resolves_disambiguation(self):
        response = self.client.get(self.url, {
            'prefix': 'VES', 'code': '1', 'date': '2026-06-15',
            'product_id': str(self.p1.pk),
        })
        self.assertFalse(response.context.get('needs_disambiguation'))
        self.assertEqual(response.context['product'], self.p1)
        self.assertTrue(response.context.get('checked'))

    def test_rented_product_shows_rental(self):
        _make_rental_with_item(self.p3)
        response = self.client.get(self.url, {'prefix': 'VES', 'code': '2', 'date': '2026-06-05'})
        self.assertIsNotNone(response.context.get('rental'))

    def test_available_product_returns_no_rental(self):
        response = self.client.get(self.url, {'prefix': 'VES', 'code': '2', 'date': '2026-07-01'})
        self.assertIsNone(response.context.get('rental'))


# ── R8.04 Product history ─────────────────────────────────────────────────────

class ProductHistoryViewTests(TestCase):
    def setUp(self):
        self.cat_a, self.cat_b, self.p1, self.p2, self.p3, self.p4 = _make_catalog()
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_200_renders(self):
        response = self.client.get(reverse('catalog:product_history', args=[self.p3.pk]))
        self.assertEqual(response.status_code, 200)

    def test_shows_rental_items(self):
        rental = _make_rental_with_item(self.p3)
        response = self.client.get(reverse('catalog:product_history', args=[self.p3.pk]))
        items = list(response.context['rental_items'])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].rental.pk, rental.pk)

    def test_shows_sibling_warning_for_duplicates(self):
        response = self.client.get(reverse('catalog:product_history', args=[self.p1.pk]))
        siblings = list(response.context['siblings'])
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0].pk, self.p2.pk)

    def test_no_siblings_for_unique_product(self):
        response = self.client.get(reverse('catalog:product_history', args=[self.p3.pk]))
        siblings = list(response.context['siblings'])
        self.assertEqual(siblings, [])


# ── R8.05 Placeholder review ──────────────────────────────────────────────────

class PlaceholderReviewViewTests(TestCase):
    def setUp(self):
        _make_catalog()
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_200_renders(self):
        response = self.client.get(reverse('catalog:placeholder_review'))
        self.assertEqual(response.status_code, 200)

    def test_lists_placeholder_categories(self):
        response = self.client.get(reverse('catalog:placeholder_review'))
        cats = list(response.context['placeholder_categories'])
        prefixes = [c.prefix for c in cats]
        self.assertIn('TRN', prefixes)
        self.assertNotIn('VES', prefixes)

    def test_lists_placeholder_products(self):
        response = self.client.get(reverse('catalog:placeholder_review'))
        prods = list(response.context['placeholder_products'])
        self.assertEqual(len(prods), 1)
        self.assertEqual(prods[0].description, 'Terno preto')


# ── R8.06 Category merge ──────────────────────────────────────────────────────

class CategoryMergeViewTests(TestCase):
    def setUp(self):
        self.cat_a, self.cat_b, self.p1, self.p2, self.p3, self.p4 = _make_catalog()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('catalog:category_merge')

    def test_get_form_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)

    def test_post_shows_preview(self):
        response = self.client.post(self.url, {
            'source': self.cat_b.pk,
            'target': self.cat_a.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context.get('preview'))
        preview = response.context['preview']
        self.assertEqual(preview['product_count'], 1)

    def test_post_confirmed_merges(self):
        response = self.client.post(self.url, {
            'source': self.cat_b.pk,
            'target': self.cat_a.pk,
            'confirmed': '1',
        })
        self.assertRedirects(response, reverse('catalog:category_list'))
        # All products from cat_b should now be in cat_a
        self.assertEqual(Product.objects.filter(category=self.cat_b).count(), 0)
        self.assertTrue(Product.objects.filter(category=self.cat_a, code=10).exists())

    def test_same_source_target_invalid(self):
        response = self.client.post(self.url, {
            'source': self.cat_a.pk,
            'target': self.cat_a.pk,
        })
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertFalse(form.is_valid())


# ── R8.07 Product value suggestion ────────────────────────────────────────────

class ProductValueSuggestionTests(TestCase):
    def setUp(self):
        self.cat_a, _, self.p1, self.p2, self.p3, _ = _make_catalog()
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_product_form_has_value_help_text(self):
        response = self.client.get(reverse('catalog:product_update', args=[self.p3.pk]))
        form = response.context['form']
        self.assertIn('Não altera o valor já cobrado', form.fields['value'].help_text)

    def test_rental_item_value_independent_of_product_value(self):
        """Changing Product.value must not change existing RentalItem.value."""
        rental = _make_rental_with_item(self.p3)
        item = RentalItem.objects.get(rental=rental, product=self.p3)
        original_item_value = item.value
        # Update product value
        self.p3.value = Decimal('999')
        self.p3.save()
        item.refresh_from_db()
        self.assertEqual(item.value, original_item_value)
