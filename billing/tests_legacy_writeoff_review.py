import csv
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from billing.models import Receivable
from billing.services import (
    legacy_writeoff_review_queryset,
    write_off_receivable,
)
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental


class LegacyWriteoffReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        customer = Customer.objects.create(name='Cliente de revisão')
        cls.rental = Rental.objects.create(
            number=56410,
            customer=customer,
            pickup_date=date(2026, 7, 1),
            return_date=date(2026, 7, 2),
        )

    def make_receivable(self, *, amount, paid_amount):
        return Receivable.objects.create(
            rental=self.rental,
            due_date=date(2026, 7, 10),
            amount=amount,
            paid_amount=paid_amount,
        )

    def record_legacy_write_off(self, receivable):
        Receivable.objects.filter(pk=receivable.pk).update(
            written_off_at=timezone.now(),
            written_off_reason='Baixa da migração legada',
            balance=Decimal('0.00'),
        )
        AuditLog.record(
            user=None,
            action='legacy_reset_write_off',
            obj=receivable,
            reason='Baixa da migração legada',
        )

    def test_legacy_write_off_with_residual_balance_is_included(self):
        receivable = self.make_receivable(
            amount=Decimal('300.00'),
            paid_amount=Decimal('100.00'),
        )
        self.record_legacy_write_off(receivable)

        result = legacy_writeoff_review_queryset().get()

        self.assertEqual(result, receivable)
        self.assertEqual(result.hidden_balance, Decimal('200.00'))

    def test_manual_write_off_with_residual_balance_is_excluded(self):
        receivable = self.make_receivable(
            amount=Decimal('300.00'),
            paid_amount=Decimal('100.00'),
        )
        write_off_receivable(receivable, 'Baixa manual')

        self.assertFalse(
            legacy_writeoff_review_queryset().filter(pk=receivable.pk).exists()
        )

    def test_fully_paid_legacy_write_off_is_excluded(self):
        receivable = self.make_receivable(
            amount=Decimal('300.00'),
            paid_amount=Decimal('300.00'),
        )
        self.record_legacy_write_off(receivable)

        self.assertFalse(
            legacy_writeoff_review_queryset().filter(pk=receivable.pk).exists()
        )

    def test_command_runs_without_error(self):
        output = StringIO()

        call_command('legacy_writeoff_review', stdout=output)

        self.assertIn('Locação', output.getvalue())
        self.assertIn('Total: 0 recebível(is).', output.getvalue())

    def test_command_exports_csv(self):
        receivable = self.make_receivable(
            amount=Decimal('300.00'),
            paid_amount=Decimal('100.00'),
        )
        self.record_legacy_write_off(receivable)

        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / 'legacy-writeoffs.csv'
            call_command(
                'legacy_writeoff_review',
                '--csv',
                str(csv_path),
                stdout=StringIO(),
            )

            with csv_path.open(encoding='utf-8', newline='') as csv_file:
                rows = list(csv.reader(csv_file))

        self.assertEqual(rows[0], [
            'rental_number',
            'customer_name',
            'due_date',
            'amount',
            'paid_amount',
            'hidden_balance',
            'written_off_at',
        ])
        self.assertEqual(rows[1][0], '56410')
        self.assertEqual(rows[1][1], 'CLIENTE DE REVISÃO')
        self.assertEqual(Decimal(rows[1][5]), Decimal('200.00'))
