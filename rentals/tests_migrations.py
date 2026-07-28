from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RentalProductSnapshotMigrationTests(TransactionTestCase):
    migrate_from = [
        ('catalog', '0007_product_is_active'),
        ('customers', '0009_customer_alternate_phone_contact'),
        ('rentals', '0008_rentalitem_proof_photo_filefield'),
    ]
    migrate_to = [
        ('catalog', '0007_product_is_active'),
        ('customers', '0009_customer_alternate_phone_contact'),
        ('rentals', '0009_rentalitem_product_snapshot_remove_use_for'),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        Customer = old_apps.get_model('customers', 'Customer')
        Category = old_apps.get_model('catalog', 'Category')
        Product = old_apps.get_model('catalog', 'Product')
        Rental = old_apps.get_model('rentals', 'Rental')
        RentalItem = old_apps.get_model('rentals', 'RentalItem')

        customer = Customer.objects.create(name='Maria')
        category = Category.objects.create(prefix='VN', name='Vestidos')
        product = Product.objects.create(
            category=category,
            code=12,
            description='Vestido clássico',
            color='Azul',
            size='M',
            value=Decimal('300'),
        )
        rental = Rental.objects.create(
            number=300,
            customer=customer,
            pickup_date=date(2026, 8, 10),
            return_date=date(2026, 8, 15),
            use_for='Festa de formatura',
            legacy_notes='origem: teste',
        )
        RentalItem.objects.create(
            rental=rental,
            product=product,
            value=Decimal('300'),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_preserves_legacy_use_and_backfills_product_snapshot(self):
        Rental = self.apps.get_model('rentals', 'Rental')
        RentalItem = self.apps.get_model('rentals', 'RentalItem')

        rental = Rental.objects.get(number=300)
        item = RentalItem.objects.get(rental=rental)

        self.assertIn('origem: teste', rental.legacy_notes)
        self.assertIn('locado.usar: Festa de formatura', rental.legacy_notes)
        self.assertTrue(item.product_snapshot_captured)
        self.assertEqual(item.product_prefix_snapshot, 'VN')
        self.assertEqual(item.product_code_snapshot, 12)
        self.assertEqual(item.product_description_snapshot, 'Vestido clássico')
        self.assertEqual(item.product_color_snapshot, 'Azul')
        self.assertEqual(item.product_size_snapshot, 'M')

    def test_reverse_restores_use_for_and_reapply_does_not_duplicate_notes(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldRental = old_apps.get_model('rentals', 'Rental')

        restored = OldRental.objects.get(number=300)
        self.assertEqual(restored.use_for, 'Festa de formatura')
        self.assertEqual(
            restored.legacy_notes.count('locado.usar: Festa de formatura'),
            1,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        reapplied_apps = executor.loader.project_state(self.migrate_to).apps
        ReappliedRental = reapplied_apps.get_model('rentals', 'Rental')

        reapplied = ReappliedRental.objects.get(number=300)
        self.assertEqual(
            reapplied.legacy_notes.count('locado.usar: Festa de formatura'),
            1,
        )
