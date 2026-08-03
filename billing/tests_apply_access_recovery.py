import json
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from billing.management.commands import apply_access_recovery
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental


class ApplyAccessRecoveryTests(TestCase):
    def setUp(self):
        CashAccount.objects.create(name='Caixa de teste', active=True)
        customer = Customer.objects.create(name='Cliente de teste')
        rental = Rental.objects.create(
            number=1,
            customer=customer,
            pickup_date=date(2026, 7, 1),
            return_date=date(2026, 7, 2),
        )
        self.payment_receivable = Receivable.objects.create(
            rental=rental,
            due_date=date(2026, 7, 2),
            amount=Decimal('5.00'),
            paid_amount=Decimal('5.00'),
            legacy_source='pagar',
            legacy_id=101,
        )
        self.cutoff_receivable = Receivable.objects.create(
            rental=rental,
            due_date=date(2026, 7, 17),
            amount=Decimal('20.00'),
            legacy_source='pagar',
            legacy_id=102,
        )
        self.settled_receivable = Receivable.objects.create(
            rental=rental,
            due_date=date(2026, 7, 18),
            amount=Decimal('30.00'),
            legacy_source='pagar',
            legacy_id=103,
        )
        self.overpayment_receivable = Receivable.objects.create(
            rental=rental,
            due_date=date(2026, 7, 18),
            amount=Decimal('10.00'),
            paid_amount=Decimal('15.00'),
            legacy_source='pagar',
            legacy_id=104,
        )

    def write_manifest(self, root):
        payload = {
            'mode': 'access-primary-recovery',
            'cutoff_date': '2026-07-17',
            'payments': [{
                'source_payment': {'id': 1},
                'candidate_legacy': {'source': 'pagar', 'id': 101},
                'amount': '5.00',
                'payment_date': '2026-07-02',
                'method': 'other',
                'discount_amount': '0.00',
                'interest_amount': '0.00',
                'group': 'legacy_receivable',
            }],
            'write_offs': {
                'access_settled_without_value_ids': [103],
                'legacy_overpayment_ids': [104],
            },
        }
        payload['sha256'] = apply_access_recovery._canonical_sha256(payload)
        manifest = root / 'access-recovery.json'
        manifest.write_text(json.dumps(payload), encoding='utf-8')
        return manifest, payload['sha256']

    def run_command(self, manifest, *args):
        output = StringIO()
        call_command(
            'apply_access_recovery',
            '--manifest',
            str(manifest),
            *args,
            stdout=output,
        )
        return output.getvalue()

    def approved_manifest(self, manifest_sha256):
        return patch.multiple(
            apply_access_recovery,
            APPROVED_MANIFEST_SHA256=manifest_sha256,
            APPROVED_SUMMARY=(1, Decimal('5.00'), 3),
            APPROVED_PAYMENT_GROUPS={'legacy_receivable': 1},
            APPROVED_WRITE_OFF_GROUPS=(1, 1, 1),
        )

    def test_dry_run_validates_without_mutating(self):
        with TemporaryDirectory() as directory, override_settings(
            BACKUP_ROOT=Path(directory) / 'backups',
        ):
            root = Path(directory) / 'recovery-manifests'
            root.mkdir()
            manifest, manifest_sha256 = self.write_manifest(root)
            with self.approved_manifest(manifest_sha256):
                output = self.run_command(manifest)

        self.assertIn('DRY-RUN', output)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(Receivable.objects.filter(written_off_at__isnull=False).exists())

    def test_apply_registers_payments_and_audited_write_offs(self):
        with TemporaryDirectory() as directory, override_settings(
            BACKUP_ROOT=Path(directory) / 'backups',
        ):
            root = Path(directory) / 'recovery-manifests'
            root.mkdir()
            manifest, manifest_sha256 = self.write_manifest(root)
            with self.approved_manifest(manifest_sha256):
                output = self.run_command(manifest, '--apply', '--confirm')

        self.assertIn('Recuperação concluída', output)
        self.payment_receivable.refresh_from_db()
        self.cutoff_receivable.refresh_from_db()
        self.settled_receivable.refresh_from_db()
        self.overpayment_receivable.refresh_from_db()
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(
            FinancialMovement.objects.filter(
                source=FinancialMovement.Source.PAYMENT,
            ).count(),
            1,
        )
        self.assertEqual(self.payment_receivable.paid_amount, Decimal('5.00'))
        self.assertEqual(self.cutoff_receivable.balance, Decimal('0.00'))
        self.assertEqual(self.settled_receivable.balance, Decimal('0.00'))
        self.assertEqual(self.overpayment_receivable.balance, Decimal('0.00'))
        self.assertEqual(AuditLog.objects.count(), 3)

    def test_rejects_unapproved_manifest_before_writing(self):
        with TemporaryDirectory() as directory, override_settings(
            BACKUP_ROOT=Path(directory) / 'backups',
        ):
            root = Path(directory) / 'recovery-manifests'
            root.mkdir()
            manifest, _manifest_sha256 = self.write_manifest(root)
            with self.assertRaisesMessage(Exception, 'Manifesto não corresponde'):
                self.run_command(manifest)

        self.assertFalse(Payment.objects.exists())
