"""Tests for the legacy_reset management command and receivable write-off."""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from billing.models import Receivable
from billing.services import reconcile_financial
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental


def _make_rental(number, status, pickup_date, customer):
    return Rental.objects.create(
        number=number,
        customer=customer,
        pickup_date=pickup_date,
        return_date=pickup_date,
        status=status,
        total_value=Decimal('100.00'),
    )


class ReceivableWriteOffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = Customer.objects.create(name='Cliente Teste')
        cls.rental = _make_rental(1, Rental.Status.RETURNED, date(2020, 1, 10), cls.customer)

    def test_write_off_forces_zero_balance_on_save(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2020, 2, 10), amount=Decimal('80.00'),
        )
        self.assertEqual(rec.balance, Decimal('80.00'))
        rec.written_off_at = timezone.now()
        rec.save()
        self.assertEqual(rec.balance, Decimal('0'))
        self.assertTrue(rec.is_written_off)
        self.assertTrue(rec.is_paid)

    def test_recalculate_from_payments_keeps_write_off(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2020, 2, 10), amount=Decimal('80.00'),
            written_off_at=timezone.now(),
        )
        rec.recalculate_from_payments()
        self.assertEqual(rec.balance, Decimal('0'))

    def test_clearing_write_off_restores_balance(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2020, 2, 10), amount=Decimal('80.00'),
            written_off_at=timezone.now(),
        )
        rec.written_off_at = None
        rec.save()
        self.assertEqual(rec.balance, Decimal('80.00'))

    def test_reconciliation_excludes_written_off(self):
        Receivable.objects.create(
            rental=self.rental, due_date=date(2020, 2, 10), amount=Decimal('80.00'),
            written_off_at=timezone.now(),
        )
        recon = reconcile_financial()
        self.assertEqual(recon['paid_no_payments_count'], 0)


class LegacyResetCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = Customer.objects.create(name='Cliente Teste')
        cls.old_pending = _make_rental(10, Rental.Status.PENDING, date(1995, 5, 1), cls.customer)
        cls.new_pending = _make_rental(11, Rental.Status.PENDING, date(2026, 7, 1), cls.customer)
        cls.picked_up = _make_rental(12, Rental.Status.PICKED_UP, date(1990, 1, 1), cls.customer)

        cls.old_open = Receivable.objects.create(
            rental=cls.old_pending, due_date=date(1995, 6, 1), amount=Decimal('50.00'),
        )
        cls.new_open = Receivable.objects.create(
            rental=cls.new_pending, due_date=date(2026, 8, 1), amount=Decimal('70.00'),
        )
        cls.old_paid = Receivable.objects.create(
            rental=cls.old_pending, due_date=date(1995, 7, 1), amount=Decimal('30.00'),
            paid_amount=Decimal('30.00'),
        )

    def _call(self, *args):
        out = StringIO()
        call_command('legacy_reset', *args, stdout=out)
        return out.getvalue()

    def test_requires_at_least_one_cutoff(self):
        with self.assertRaises(CommandError):
            call_command('legacy_reset')

    def test_invalid_date_rejected(self):
        with self.assertRaises(CommandError):
            call_command('legacy_reset', '--pickup-before', '19-05-2020')

    def test_dry_run_changes_nothing(self):
        output = self._call('--pickup-before', '2020-01-01', '--due-before', '2020-01-01')
        self.old_pending.refresh_from_db()
        self.old_open.refresh_from_db()
        self.assertEqual(self.old_pending.status, Rental.Status.PENDING)
        self.assertIsNone(self.old_open.written_off_at)
        self.assertIn('DRY-RUN', output)
        self.assertIn('Total a cancelar: 1', output)
        self.assertIn('Total a baixar: 1', output)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_apply_cancels_old_pending_only(self):
        self._call('--pickup-before', '2020-01-01', '--apply')

        self.old_pending.refresh_from_db()
        self.new_pending.refresh_from_db()
        self.picked_up.refresh_from_db()

        self.assertEqual(self.old_pending.status, Rental.Status.CANCELLED)
        self.assertIsNotNone(self.old_pending.cancelled_at)
        self.assertNotEqual(self.old_pending.cancelled_reason, '')
        # Newer pending and picked-up rentals are untouched.
        self.assertEqual(self.new_pending.status, Rental.Status.PENDING)
        self.assertEqual(self.picked_up.status, Rental.Status.PICKED_UP)

        log = AuditLog.objects.get(action='legacy_reset_cancel_rentals')
        self.assertEqual(log.metadata['cancelled_count'], 1)

    def test_apply_writes_off_old_open_only(self):
        self._call('--due-before', '2020-01-01', '--apply')

        self.old_open.refresh_from_db()
        self.new_open.refresh_from_db()
        self.old_paid.refresh_from_db()

        self.assertIsNotNone(self.old_open.written_off_at)
        self.assertEqual(self.old_open.balance, Decimal('0'))
        self.assertEqual(self.old_open.amount, Decimal('50.00'))
        self.assertEqual(self.old_open.paid_amount, Decimal('0'))
        # Newer open and already-paid receivables are untouched.
        self.assertIsNone(self.new_open.written_off_at)
        self.assertEqual(self.new_open.balance, Decimal('70.00'))
        self.assertIsNone(self.old_paid.written_off_at)

        log = AuditLog.objects.get(action='legacy_reset_write_off')
        self.assertEqual(log.metadata['written_off_count'], 1)
        self.assertEqual(log.metadata['written_off_balance'], '50.00')

    def test_apply_is_idempotent(self):
        self._call('--due-before', '2020-01-01', '--apply')
        output = self._call('--due-before', '2020-01-01', '--apply')
        self.assertIn('Total a baixar: 0', output)
        self.assertEqual(
            AuditLog.objects.filter(action='legacy_reset_write_off').count(), 1,
        )
