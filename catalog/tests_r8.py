"""Tests for Sprint R8 — catalog filters, disambiguation, history, placeholder review, merge."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from accounts.models import ActionPermission
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


class ProductSearchViewTests(TestCase):
    def setUp(self):
        self.cat_a, self.cat_b, self.p1, self.p2, self.p3, self.p4 = _make_catalog()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('catalog:product_search')

    def test_search_by_one_digit_code(self):
        response = self.client.get(self.url, {'q': '1'})

        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.json()['results']}
        self.assertIn(self.p1.pk, ids)
        self.assertIn(self.p2.pk, ids)
        self.assertNotIn(self.p3.pk, ids)

    def test_search_by_prefix_and_code(self):
        response = self.client.get(self.url, {'q': 'TRN10'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row['id'] for row in response.json()['results']],
            [self.p4.pk],
        )


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

    def test_category_product_count_excludes_archived_products(self):
        archived = Product.objects.get(description='Vestido branco')
        archived.is_active = False
        archived.save(update_fields=['is_active', 'updated_at'])

        response = self.client.get(reverse('catalog:category_list'))

        categories = {
            category.prefix: category.product_count
            for category in response.context['categories']
        }
        self.assertEqual(categories['VES'], 2)
        self.assertEqual(categories['TRN'], 1)


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
        response = self.client.get(self.url, {'prefix': 'VES', 'code': '1'})
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


class AvailabilityOperationalLookupTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('catalog:availability')
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')
        self.product = Product.objects.create(
            category=self.category,
            code=38,
            description='Vestido sereia com pala',
            color='Verde escuro',
            size='GG',
            value=Decimal('300'),
        )
        self.customer = Customer.objects.create(name='Walter Domingues')

    def make_rental(
        self,
        *,
        number,
        pickup_date,
        return_date,
        status=Rental.Status.PENDING,
        customer=None,
    ):
        rental = Rental.objects.create(
            number=number,
            customer=customer or self.customer,
            pickup_date=pickup_date,
            return_date=return_date,
            status=status,
        )
        RentalItem.objects.create(rental=rental, product=self.product, value=Decimal('300'))
        return rental

    @patch('catalog.views.timezone.localdate', return_value=date(2026, 7, 27))
    def test_prefix_and_code_without_date_show_current_and_future_rentals(self, _localdate):
        current = self.make_rental(
            number=56037,
            pickup_date=date(2026, 7, 25),
            return_date=date(2026, 8, 2),
            status=Rental.Status.PICKED_UP,
        )
        future_customer = Customer.objects.create(name='Ana Beatriz')
        future = self.make_rental(
            number=56050,
            customer=future_customer,
            pickup_date=date(2026, 9, 3),
            return_date=date(2026, 9, 8),
        )
        returned = self.make_rental(
            number=56051,
            pickup_date=date(2026, 10, 1),
            return_date=date(2026, 10, 5),
            status=Rental.Status.RETURNED,
        )
        cancelled = self.make_rental(
            number=56052,
            pickup_date=date(2026, 11, 1),
            return_date=date(2026, 11, 5),
            status=Rental.Status.CANCELLED,
        )

        response = self.client.get(self.url, {'prefix': 'vf', 'code': '0038'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['checked'])
        self.assertFalse(response.context['uses_custom_date'])
        self.assertEqual(response.context['reference_date'], date(2026, 7, 27))
        self.assertEqual(response.context['rental'], current)
        self.assertEqual(response.context['scheduled_rentals'], [current, future])
        self.assertNotIn(returned, response.context['scheduled_rentals'])
        self.assertNotIn(cancelled, response.context['scheduled_rentals'])
        self.assertContains(response, 'Locado')
        self.assertContains(response, 'Walter Domingues')
        self.assertContains(response, 'Ana Beatriz')
        self.assertContains(response, '03/09/2026')
        self.assertContains(response, '08/09/2026')

    @patch('catalog.views.timezone.localdate', return_value=date(2026, 7, 27))
    def test_future_rental_is_visible_without_marking_product_rented_today(self, _localdate):
        future = self.make_rental(
            number=56050,
            pickup_date=date(2026, 9, 3),
            return_date=date(2026, 9, 8),
        )

        response = self.client.get(self.url, {'prefix': 'VF', 'code': '38'})

        self.assertIsNone(response.context['rental'])
        self.assertEqual(response.context['scheduled_rentals'], [future])
        self.assertContains(response, 'Produto disponível hoje')
        self.assertContains(response, 'uma locação futura')

    @patch('catalog.views.timezone.localdate', return_value=date(2026, 7, 27))
    def test_picked_up_overdue_rental_remains_unavailable_until_returned(self, _localdate):
        overdue = self.make_rental(
            number=56040,
            pickup_date=date(2026, 7, 10),
            return_date=date(2026, 7, 20),
            status=Rental.Status.PICKED_UP,
        )

        response = self.client.get(self.url, {'prefix': 'VF', 'code': '38'})

        self.assertEqual(response.context['rental'], overdue)
        self.assertEqual(response.context['scheduled_rentals'], [overdue])
        self.assertContains(response, 'Produto indisponível hoje')
        self.assertContains(response, 'Em uso')

    def test_picked_up_overdue_rental_remains_unavailable_on_a_future_date(self):
        overdue = self.make_rental(
            number=56041,
            pickup_date=date(2026, 7, 10),
            return_date=date(2026, 7, 20),
            status=Rental.Status.PICKED_UP,
        )

        response = self.client.get(self.url, {
            'prefix': 'VF',
            'code': '38',
            'date': '15/08/2026',
        })

        self.assertEqual(response.context['rental'], overdue)
        self.assertContains(response, 'Produto indisponível em 15/08/2026')

    @patch('catalog.views.timezone.localdate', return_value=date(2026, 7, 27))
    def test_product_without_rentals_has_clear_available_empty_state(self, _localdate):
        response = self.client.get(self.url, {'prefix': 'VF', 'code': '38'})

        self.assertTrue(response.context['checked'])
        self.assertIsNone(response.context['rental'])
        self.assertEqual(response.context['scheduled_rentals'], [])
        self.assertContains(response, 'Disponível')
        self.assertContains(response, 'Sem locações ativas')

    def test_optional_date_remains_compatible(self):
        rental = self.make_rental(
            number=56060,
            pickup_date=date(2027, 1, 15),
            return_date=date(2027, 1, 20),
        )

        response = self.client.get(self.url, {
            'prefix': 'VF',
            'code': '38',
            'date': '16/01/2027',
        })

        self.assertTrue(response.context['uses_custom_date'])
        self.assertEqual(response.context['reference_date'], date(2027, 1, 16))
        self.assertEqual(response.context['rental'], rental)
        self.assertContains(response, 'Produto indisponível em 16/01/2027')

    def test_unknown_prefix_and_code_show_specific_not_found_message(self):
        response = self.client.get(self.url, {'prefix': 'XX', 'code': '999'})

        self.assertFalse(response.context.get('checked'))
        self.assertContains(response, 'Produto XX999 não encontrado')

    def test_invalid_code_is_rejected_without_querying_a_product(self):
        response = self.client.get(self.url, {'prefix': 'VF', 'code': '38A'})

        self.assertFalse(response.context.get('checked'))
        self.assertIn('code', response.context['form'].errors)
        self.assertContains(response, 'Informe um código de produto válido.')

    def test_missing_prefix_is_rejected(self):
        response = self.client.get(self.url, {'prefix': '', 'code': '38'})

        self.assertFalse(response.context.get('checked'))
        self.assertIn('prefix', response.context['form'].errors)

    def test_keyboard_flow_hooks_are_rendered(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'data-enter-next="availability-code"')
        self.assertContains(response, 'data-submit-on-enter="true"')
        self.assertContains(response, "form.requestSubmit(submitButton)")

    def test_date_field_is_rendered_so_future_dates_can_be_checked(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'id="availability-date"')
        self.assertContains(response, 'name="date"')


class AvailabilityAccessTests(TestCase):
    def setUp(self):
        self.url = reverse('catalog:availability')

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_authenticated_user_without_catalog_permission_is_denied(self):
        user = _make_user(module_key='rentals')
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_catalog_permission_allows_lookup(self):
        user = _make_user()
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)


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

    def test_placeholder_counts_and_list_exclude_archived_products(self):
        product = Product.objects.get(description='Terno preto')
        product.is_active = False
        product.save(update_fields=['is_active', 'updated_at'])

        response = self.client.get(reverse('catalog:placeholder_review'))

        category = response.context['placeholder_categories'].get(prefix='TRN')
        self.assertEqual(category.product_count, 0)
        self.assertEqual(list(response.context['placeholder_products']), [])


# ── R8.06 Category merge ──────────────────────────────────────────────────────

class CategoryMergeViewTests(TestCase):
    def setUp(self):
        self.cat_a, self.cat_b, self.p1, self.p2, self.p3, self.p4 = _make_catalog()
        self.user = _make_user()
        ActionPermission.objects.create(
            user=self.user,
            action_key='catalog.delete',
            allowed=True,
        )
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

    def test_post_requires_catalog_delete_action(self):
        ActionPermission.objects.filter(
            user=self.user,
            action_key='catalog.delete',
        ).update(allowed=False)

        response = self.client.post(self.url, {
            'source': self.cat_b.pk,
            'target': self.cat_a.pk,
            'confirmed': '1',
        })

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Category.objects.filter(pk=self.cat_b.pk).exists())


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
