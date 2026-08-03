from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from billing.models import Receivable
from billing.services import (
    reconcile_overpayment,
    write_off_receivable,
    write_off_receivables,
)
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental


class ReconcileNegativeBalancesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        customer = Customer.objects.create(name='Cliente Teste')
        cls.rental = Rental.objects.create(
            number=1,
            customer=customer,
            pickup_date=date(2008, 10, 1),
            return_date=date(2008, 10, 2),
            status=Rental.Status.RETURNED,
        )

    def make_receivable(self, *, amount, paid_amount):
        return Receivable.objects.create(
            rental=self.rental,
            due_date=date(2008, 10, 4),
            amount=amount,
            paid_amount=paid_amount,
        )

    def call_command(self, *args):
        stdout = StringIO()
        call_command('reconcile_negative_balances', *args, stdout=stdout)
        return stdout.getvalue()

    def test_negative_balance_is_zeroed_and_audited(self):
        receivable = self.make_receivable(
            amount=Decimal('15.00'),
            paid_amount=Decimal('30.00'),
        )

        changed = reconcile_overpayment(receivable, 'Crédito legado prescrito.')

        self.assertTrue(changed)
        receivable.refresh_from_db()
        self.assertEqual(receivable.balance, Decimal('0.00'))
        self.assertIsNotNone(receivable.written_off_at)
        self.assertEqual(
            receivable.written_off_reason,
            'Crédito legado prescrito.',
        )
        log = AuditLog.objects.get(action='reconcile_overpayment')
        self.assertEqual(log.model_name, 'Receivable')
        self.assertEqual(log.object_id, str(receivable.pk))
        self.assertEqual(log.metadata['previous_balance'], '-15.00')
        self.assertEqual(log.metadata['overpayment_amount'], '15.00')

    def test_positive_balance_write_off_is_zeroed_and_audited(self):
        receivable = self.make_receivable(
            amount=Decimal('20.00'),
            paid_amount=Decimal('5.00'),
        )

        changed = write_off_receivable(receivable, 'Título legado prescrito.')

        self.assertTrue(changed)
        receivable.refresh_from_db()
        self.assertEqual(receivable.balance, Decimal('0.00'))
        self.assertIsNotNone(receivable.written_off_at)
        self.assertEqual(receivable.written_off_reason, 'Título legado prescrito.')
        log = AuditLog.objects.get(action='write_off_receivable')
        self.assertEqual(log.metadata['previous_balance'], '15.00')

    def test_write_off_ignores_already_closed_receivable(self):
        receivable = self.make_receivable(
            amount=Decimal('20.00'),
            paid_amount=Decimal('20.00'),
        )

        changed = write_off_receivable(receivable, 'Título já encerrado.')

        self.assertFalse(changed)
        self.assertIsNone(receivable.written_off_at)
        self.assertFalse(AuditLog.objects.exists())

    def test_batch_write_off_is_atomic_and_audited_per_receivable(self):
        first = self.make_receivable(
            amount=Decimal('20.00'),
            paid_amount=Decimal('5.00'),
        )
        second = self.make_receivable(
            amount=Decimal('30.00'),
            paid_amount=Decimal('0.00'),
        )
        closed = self.make_receivable(
            amount=Decimal('10.00'),
            paid_amount=Decimal('10.00'),
        )

        changed = write_off_receivables(
            [first, second, closed],
            'Baixa em lote testada.',
        )

        self.assertEqual(changed, 2)
        first.refresh_from_db()
        second.refresh_from_db()
        closed.refresh_from_db()
        self.assertEqual(first.balance, Decimal('0.00'))
        self.assertEqual(second.balance, Decimal('0.00'))
        self.assertIsNone(closed.written_off_at)
        logs = AuditLog.objects.filter(action='write_off_receivable')
        self.assertEqual(logs.count(), 2)
        self.assertEqual(
            {log.metadata['previous_balance'] for log in logs},
            {'15.00', '30.00'},
        )

    def test_apply_is_idempotent(self):
        receivable = self.make_receivable(
            amount=Decimal('15.00'),
            paid_amount=Decimal('30.00'),
        )

        first_output = self.call_command('--apply')
        second_output = self.call_command('--apply')

        receivable.refresh_from_db()
        self.assertEqual(receivable.balance, Decimal('0.00'))
        self.assertIn('Reconciliados: 1', first_output)
        self.assertIn('Total a reconciliar: 0', second_output)
        self.assertEqual(
            AuditLog.objects.filter(action='reconcile_overpayment').count(),
            1,
        )

    def test_nonnegative_balances_are_untouched(self):
        open_receivable = self.make_receivable(
            amount=Decimal('20.00'),
            paid_amount=Decimal('5.00'),
        )
        paid_receivable = self.make_receivable(
            amount=Decimal('20.00'),
            paid_amount=Decimal('20.00'),
        )

        output = self.call_command('--apply')

        open_receivable.refresh_from_db()
        paid_receivable.refresh_from_db()
        self.assertEqual(open_receivable.balance, Decimal('15.00'))
        self.assertEqual(paid_receivable.balance, Decimal('0.00'))
        self.assertIsNone(open_receivable.written_off_at)
        self.assertIsNone(paid_receivable.written_off_at)
        self.assertIn('Total a reconciliar: 0', output)
        self.assertFalse(AuditLog.objects.exists())

    def test_default_mode_is_dry_run(self):
        receivable = self.make_receivable(
            amount=Decimal('15.00'),
            paid_amount=Decimal('30.00'),
        )

        output = self.call_command()

        receivable.refresh_from_db()
        self.assertEqual(receivable.balance, Decimal('-15.00'))
        self.assertIsNone(receivable.written_off_at)
        self.assertIn('DRY-RUN', output)
        self.assertFalse(AuditLog.objects.exists())
