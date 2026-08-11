"""Tests for the classification rule used by migration 0019.

The pure-function tests exercise ``classify_financial_policy`` and
``find_import_batch_minute`` directly, with no database access — they
document and lock in the rule that replaces the old (and wrong)
``pk == number`` heuristic for deciding whether a rental predates the
Access import. The database test exercises the real backfill function
end-to-end against ordinary ORM records.
"""
from datetime import date, timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from billing.models import Payment, Receivable
from customers.models import Customer
from rentals.models import Rental

backfill = import_module('rentals.migrations.0019_rental_financial_policy_version')


class ClassifyFinancialPolicyTests(SimpleTestCase):
    def test_rental_with_legacy_mark_is_legacy_access(self):
        result = backfill.classify_financial_policy(
            has_legacy_mark=True,
            created_at=timezone.datetime(2026, 8, 4, 12, 0),
            import_batch_minute=None,
        )
        self.assertEqual(result, backfill.LEGACY_ACCESS)

    def test_legacy_mark_wins_even_inside_the_import_batch_minute(self):
        batch_minute = timezone.datetime(2026, 8, 2, 15, 0)
        result = backfill.classify_financial_policy(
            has_legacy_mark=True,
            created_at=batch_minute,
            import_batch_minute=batch_minute,
        )
        self.assertEqual(result, backfill.LEGACY_ACCESS)

    def test_new_rental_without_legacy_mark_is_enforced_v1(self):
        result = backfill.classify_financial_policy(
            has_legacy_mark=False,
            created_at=timezone.datetime(2026, 8, 10, 8, 0),
            import_batch_minute=None,
        )
        self.assertEqual(result, backfill.ENFORCED_V1)

    def test_unmarked_rental_created_inside_import_batch_minute_is_legacy_access(self):
        """Covers the 130 marker-free import rows created in the same minute."""
        batch_minute = timezone.datetime(2026, 8, 2, 15, 0)
        result = backfill.classify_financial_policy(
            has_legacy_mark=False,
            created_at=batch_minute + timedelta(seconds=45),
            import_batch_minute=batch_minute,
        )
        self.assertEqual(result, backfill.LEGACY_ACCESS)

    def test_real_operator_rental_on_the_same_day_is_enforced_v1(self):
        """Covers the 12 real rentals created shortly after the import."""
        batch_minute = timezone.datetime(2026, 8, 2, 15, 0)
        result = backfill.classify_financial_policy(
            has_legacy_mark=False,
            created_at=timezone.datetime(2026, 8, 3, 10, 0),
            import_batch_minute=batch_minute,
        )
        self.assertEqual(result, backfill.ENFORCED_V1)

    def test_boundary_one_minute_after_the_batch_is_enforced_v1(self):
        batch_minute = timezone.datetime(2026, 8, 2, 15, 0)
        result = backfill.classify_financial_policy(
            has_legacy_mark=False,
            created_at=batch_minute + timedelta(minutes=1),
            import_batch_minute=batch_minute,
        )
        self.assertEqual(result, backfill.ENFORCED_V1)

    def test_no_import_batch_detected_falls_back_to_the_mark_only(self):
        result = backfill.classify_financial_policy(
            has_legacy_mark=False,
            created_at=timezone.datetime(2026, 8, 2, 15, 0),
            import_batch_minute=None,
        )
        self.assertEqual(result, backfill.ENFORCED_V1)


class FindImportBatchMinuteTests(SimpleTestCase):
    def test_empty_set_does_not_explode(self):
        self.assertIsNone(backfill.find_import_batch_minute({}))

    def test_picks_the_minute_with_the_largest_batch(self):
        import_minute = timezone.datetime(2026, 8, 2, 15, 0)
        ordinary_minute = timezone.datetime(2026, 8, 3, 9, 30)
        counts = {import_minute: 35887, ordinary_minute: 1}
        self.assertEqual(
            backfill.find_import_batch_minute(counts),
            import_minute,
        )

    def test_single_minute_set_returns_that_minute(self):
        only_minute = timezone.datetime(2026, 8, 5, 11, 0)
        self.assertEqual(
            backfill.find_import_batch_minute({only_minute: 1}),
            only_minute,
        )


class FinancialPolicyBackfillEmptyDatabaseTests(TransactionTestCase):
    """Requirement: the backfill must not explode on an empty database."""

    def test_empty_database_does_not_raise(self):
        executor = MigrationExecutor(connection)
        apps = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps

        backfill.backfill_financial_policy_version(
            apps,
            SimpleNamespace(connection=connection),
        )


class FinancialPolicyBackfillDatabaseTests(TransactionTestCase):
    """End-to-end check of the backfill against ordinary ORM records."""

    def test_marks_and_derived_import_batch_are_backfilled(self):
        customer = Customer.objects.create(name='Cliente do backfill')
        rental_kwargs = {
            'customer': customer,
            'pickup_date': date(2026, 8, 20),
            'return_date': date(2026, 8, 22),
            'total_value': Decimal('100.00'),
        }
        marked_by_notes = Rental.objects.create(
            number=101,
            legacy_notes='origem: Access',
            **rental_kwargs,
        )
        marked_by_receivable = Rental.objects.create(number=102, **rental_kwargs)
        marked_by_payment = Rental.objects.create(number=103, **rental_kwargs)
        import_batch_without_marker = Rental.objects.create(number=105, **rental_kwargs)
        new_rental = Rental.objects.create(number=106, **rental_kwargs)

        Receivable.objects.create(
            rental=marked_by_receivable,
            due_date=date(2026, 8, 20),
            amount=Decimal('100.00'),
            legacy_source='external-import',
        )
        payment_receivable = Receivable.objects.create(
            rental=marked_by_payment,
            due_date=date(2026, 8, 20),
            amount=Decimal('100.00'),
        )
        Payment.objects.create(
            receivable=payment_receivable,
            rental=marked_by_payment,
            payment_date=date(2026, 8, 20),
            amount=Decimal('10.00'),
            legacy_movement_id=9001,
        )

        # Two of the marked rentals plus the marker-free one all land in the
        # same creation minute, so that minute becomes the derived import
        # batch; the plain new rental is created a minute later and must
        # stay ENFORCED_V1 even though it shares the same day.
        import_minute = timezone.now().replace(second=0, microsecond=0)
        Rental.objects.filter(
            pk__in=[
                marked_by_notes.pk,
                marked_by_receivable.pk,
                marked_by_payment.pk,
                import_batch_without_marker.pk,
            ],
        ).update(created_at=import_minute)
        Rental.objects.filter(pk=new_rental.pk).update(
            created_at=import_minute + timedelta(minutes=1),
        )

        executor = MigrationExecutor(connection)
        apps = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        backfill.backfill_financial_policy_version(
            apps,
            SimpleNamespace(connection=connection),
        )

        policies = dict(
            Rental.objects.filter(
                pk__in=[
                    marked_by_notes.pk,
                    marked_by_receivable.pk,
                    marked_by_payment.pk,
                    import_batch_without_marker.pk,
                    new_rental.pk,
                ],
            ).values_list('pk', 'financial_policy_version')
        )
        self.assertEqual(policies[marked_by_notes.pk], backfill.LEGACY_ACCESS)
        self.assertEqual(policies[marked_by_receivable.pk], backfill.LEGACY_ACCESS)
        self.assertEqual(policies[marked_by_payment.pk], backfill.LEGACY_ACCESS)
        self.assertEqual(
            policies[import_batch_without_marker.pk],
            backfill.LEGACY_ACCESS,
        )
        self.assertEqual(policies[new_rental.pk], backfill.ENFORCED_V1)
