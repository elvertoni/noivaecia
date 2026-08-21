"""Reusing a code must not make an old rental unfindable.

``RentalItem`` freezes a snapshot of the piece when the line is created, so a
contract printed years ago keeps showing the garment that was actually rented
even after the code is reused by another piece.  The operational searches have
to honour that same snapshot — otherwise the rental prints one thing and is
found by another.

Also covers the guards that keep one live item per code on paths that do not go
through ``ProductForm``: the category merge, and migration ``catalog.0009``
itself.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db.migrations.executor import MigrationExecutor
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from catalog.models import Category, Product
from catalog.services import LEGACY_FREED_MARKER
from customers.models import Customer
from rentals.models import Rental, RentalItem

User = get_user_model()


def grant(email, *modules, actions=()):
    user = User.objects.create_user(email=email, password='Senha12345')
    for module in modules:
        ModulePermission.objects.create(user=user, module_key=module, allowed=True)
    for action in actions:
        ActionPermission.objects.create(user=user, action_key=action, allowed=True)
    return user


class HistoricalSearchAfterReuseTests(TestCase):
    """Search has to match the era name as well as the current one."""

    def setUp(self):
        self.user = grant('search@test.com', 'customers', 'rentals', 'movements')
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Marina')
        self.category = Category.objects.create(prefix='VF', name='Vestidos de festa')
        self.product = Product.objects.create(
            category=self.category, code=731, description='Vestido antigo de renda',
            color='Rosa', value=300,
        )

    def rent(self, *, number, status=Rental.Status.PENDING):
        rental = Rental.objects.create(
            number=number,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 20),
            status=status,
        )
        RentalItem.objects.create(rental=rental, product=self.product, value=300)
        return rental

    def reuse_code_for_another_piece(self):
        """Retire the code and let a different garment take it over."""
        self.product.description = 'Vestido novo tomara que caia'
        self.product.color = 'Preto'
        self.product.save()

    def test_customer_history_finds_the_rental_by_the_name_it_was_contracted_with(self):
        rental = self.rent(number=1)
        self.reuse_code_for_another_piece()

        response = self.client.get(
            reverse('customers:detail', args=[self.customer.pk]),
            {'product': 'renda'},
        )

        self.assertEqual([r.pk for r in response.context['rentals']], [rental.pk])

    def test_customer_history_also_finds_it_by_the_current_name(self):
        rental = self.rent(number=1)
        self.reuse_code_for_another_piece()

        response = self.client.get(
            reverse('customers:detail', args=[self.customer.pk]),
            {'product': 'tomara que caia'},
        )

        self.assertEqual([r.pk for r in response.context['rentals']], [rental.pk])

    def test_customer_history_does_not_duplicate_a_rental_matching_both_names(self):
        """The OR spans one relation; ``distinct()`` must keep the rental single."""
        rental = self.rent(number=1)

        response = self.client.get(
            reverse('customers:detail', args=[self.customer.pk]),
            {'product': 'Vestido'},
        )

        self.assertEqual([r.pk for r in response.context['rentals']], [rental.pk])

    def test_pickup_list_finds_the_rental_by_its_era_name(self):
        rental = self.rent(number=2)
        self.reuse_code_for_another_piece()

        response = self.client.get(reverse('movements:pickup_list'), {'product': 'renda'})

        self.assertEqual([r.pk for r in response.context['rentals']], [rental.pk])

    def test_return_list_finds_the_rental_by_its_era_name(self):
        rental = self.rent(number=3, status=Rental.Status.PICKED_UP)
        self.reuse_code_for_another_piece()

        response = self.client.get(reverse('movements:return_list'), {'product': 'renda'})

        self.assertEqual([r.pk for r in response.context['rentals']], [rental.pk])

    def test_movement_lists_still_find_the_rental_by_prefix(self):
        rental = self.rent(number=4)

        response = self.client.get(reverse('movements:pickup_list'), {'product': 'VF'})

        self.assertEqual([r.pk for r in response.context['rentals']], [rental.pk])


class CategoryMergeCodeCollisionTests(TestCase):
    """Merging categories must not put two live items on one code."""

    def setUp(self):
        self.user = grant('merge@test.com', 'catalog', actions=('catalog.delete',))
        self.client.force_login(self.user)
        self.source = Category.objects.create(prefix='VES', name='Vestidos')
        self.target = Category.objects.create(prefix='VF', name='Vestidos de festa')
        self.url = reverse('catalog:category_merge')

    def merge(self):
        return self.client.post(self.url, {
            'source': self.source.pk,
            'target': self.target.pk,
            'confirmed': '1',
        })

    def test_merge_is_refused_when_both_categories_use_the_same_code(self):
        Product.objects.create(
            category=self.source, code=10, description='Vestido origem', value=100,
        )
        Product.objects.create(
            category=self.target, code=10, description='Vestido destino', value=100,
        )

        response = self.client.post(self.url, {
            'source': self.source.pk,
            'target': self.target.pk,
            'confirmed': '1',
        })

        self.assertEqual(
            Product.objects.filter(category=self.source, code=10).count(), 1,
        )
        self.assertTrue(Category.objects.filter(pk=self.source.pk).exists())
        self.assertIn(
            'A mesclagem criaria códigos repetidos em VF: VF10',
            ' '.join(str(m) for m in get_messages(response.wsgi_request)),
        )

    def test_a_retired_row_on_the_target_code_does_not_block_the_merge(self):
        """Only live items compete for a code; retired rows may pile up."""
        moving = Product.objects.create(
            category=self.source, code=10, description='Vestido origem', value=100,
        )
        Product.objects.create(
            category=self.target, code=10, description='NULO', value=0, is_active=False,
        )

        self.merge()

        moving.refresh_from_db()
        self.assertEqual(moving.category_id, self.target.pk)

    def test_merge_without_collisions_still_works(self):
        moving = Product.objects.create(
            category=self.source, code=10, description='Vestido origem', value=100,
        )
        Product.objects.create(
            category=self.target, code=20, description='Outro', value=100,
        )

        self.merge()

        moving.refresh_from_db()
        self.assertEqual(moving.category_id, self.target.pk)
        self.assertFalse(Category.objects.filter(pk=self.source.pk).exists())


class FreeLegacyNullCodesMigrationTests(TransactionTestCase):
    """Migration ``catalog.0009`` folded the legacy dialect onto ``is_active``."""

    migrate_from = ('catalog', '0008_product_catalog_product_value_gte_0')
    migrate_to = ('catalog', '0009_free_legacy_null_codes')

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        executor.loader.build_graph()
        return executor.loader.project_state([target]).apps

    def setUp(self):
        apps = self._migrate(self.migrate_from)
        Category = apps.get_model('catalog', 'Category')
        Product = apps.get_model('catalog', 'Product')
        category = Category.objects.create(prefix='VF', name='Vestidos de festa')
        self.rows = {
            'nulo': Product.objects.create(
                category=category, code=1, description='NULO', value=0,
            ),
            'lowercase': Product.objects.create(
                category=category, code=2, description='nulo', value=0,
            ),
            'blank': Product.objects.create(
                category=category, code=3, description='', value=0,
            ),
            'fallback': Product.objects.create(
                category=category, code=4, description='VF4', value=0,
            ),
            # "VEST NULA MANGA" means sleeveless — a real description, not a marker.
            'nula': Product.objects.create(
                category=category, code=5, description='VEST NULA MANGA', value=0,
            ),
            'real': Product.objects.create(
                category=category, code=6, description='Vestido de renda', value=300,
            ),
            'placeholder': Product.objects.create(
                category=category, code=7, description='NULO', value=0,
                is_placeholder=True,
            ),
        }

    def tearDown(self):
        # Stopping at ``migrate_to`` would leave the app one migration behind
        # for everything that runs after this class: ``TransactionTestCase``
        # rolls back rows, never DDL, and it runs last.  Go back to the leaf so
        # the uniqueness indexes are in place for the suites that follow.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        self._migrate(executor.loader.graph.leaf_nodes('catalog')[0])

    def state(self):
        return {
            key: Product.objects.get(pk=row.pk)
            for key, row in self.rows.items()
        }

    def test_forward_retires_every_shape_of_free_slot(self):
        self._migrate(self.migrate_to)
        rows = self.state()

        for key in ('nulo', 'lowercase', 'blank', 'fallback'):
            self.assertFalse(rows[key].is_active, key)
            self.assertIn(LEGACY_FREED_MARKER, rows[key].legacy_notes, key)

    def test_forward_leaves_real_descriptions_and_placeholders_alone(self):
        self._migrate(self.migrate_to)
        rows = self.state()

        for key in ('nula', 'real', 'placeholder'):
            self.assertTrue(rows[key].is_active, key)
            self.assertNotIn(LEGACY_FREED_MARKER, rows[key].legacy_notes, key)

    def test_reverse_restores_only_what_the_migration_retired(self):
        self._migrate(self.migrate_to)
        # An item retired on purpose after the migration must stay retired.
        deliberate = Product.objects.get(pk=self.rows['real'].pk)
        deliberate.is_active = False
        deliberate.save(update_fields=['is_active'])

        self._migrate(self.migrate_from)
        rows = self.state()

        for key in ('nulo', 'lowercase', 'blank', 'fallback'):
            self.assertTrue(rows[key].is_active, key)
            self.assertNotIn(LEGACY_FREED_MARKER, rows[key].legacy_notes, key)
        self.assertFalse(Product.objects.get(pk=deliberate.pk).is_active)
