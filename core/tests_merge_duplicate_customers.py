import json
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental


def _group(cpf, ids, winner, tier='T1'):
    return {
        'cpf': cpf,
        'tier': tier,
        'ids': [{'id': pk} for pk in ids],
        'winner_suggestion': winner,
    }


class MergeDuplicateCustomersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.winner = Customer.objects.create(
            name='Maria Aparecida Silva', cpf='111.111.111-11', city='Bandeirantes',
        )
        cls.loser = Customer.objects.create(
            name='Maria AP Silva', cpf='11111111111', city='Bandeirantes',
            phone_mobile='(43)99999-0000', notes='Prefere retirar sábado.',
        )
        cls.rental = Rental.objects.create(
            number=1, customer=cls.loser,
            pickup_date=date(2020, 1, 10), return_date=date(2020, 1, 12),
        )
        receivable = Receivable.objects.create(
            rental=cls.rental, due_date=date(2020, 1, 15),
            amount=Decimal('100'), balance=Decimal('100'),
        )
        cls.payment = Payment.objects.create(
            receivable=receivable, customer=cls.loser,
            payment_date=date(2020, 1, 15), amount=Decimal('100'),
        )
        account = CashAccount.objects.create(name='Caixa')
        cls.movement = FinancialMovement.objects.create(
            date=date(2020, 1, 15), account=account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('100'), customer=cls.loser,
        )

    def run_command(self, groups, *args):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / 'tiers.json'
            json_path.write_text(json.dumps(groups), encoding='utf-8')
            stdout = StringIO()
            call_command('merge_duplicate_customers', str(json_path), *args, stdout=stdout)
        return stdout.getvalue()

    def default_groups(self):
        return [_group('11111111111', [self.winner.pk, self.loser.pk], self.winner.pk)]

    def test_dry_run_makes_no_changes(self):
        output = self.run_command(self.default_groups())

        self.loser.refresh_from_db()
        self.rental.refresh_from_db()
        self.assertTrue(self.loser.is_active)
        self.assertEqual(self.rental.customer_id, self.loser.pk)
        self.assertIn('DRY-RUN', output)
        self.assertFalse(AuditLog.objects.exists())

    def test_apply_merges_group(self):
        output = self.run_command(self.default_groups(), '--apply')

        self.winner.refresh_from_db()
        self.loser.refresh_from_db()
        self.rental.refresh_from_db()
        self.payment.refresh_from_db()
        self.movement.refresh_from_db()

        self.assertEqual(self.rental.customer_id, self.winner.pk)
        self.assertEqual(self.payment.customer_id, self.winner.pk)
        self.assertEqual(self.movement.customer_id, self.winner.pk)
        self.assertFalse(self.loser.is_active)
        self.assertIn('Mesclado no cliente #', self.loser.legacy_notes)
        # Winner keeps own data but gains the loser's phone and notes.
        self.assertEqual(self.winner.phone_mobile, '(43)99999-0000')
        self.assertIn('Prefere retirar sábado.', self.winner.notes)
        self.assertIn('Absorveu cadastro(s)', self.winner.legacy_notes)
        log = AuditLog.objects.get(action='merge_duplicate_customer')
        self.assertEqual(log.object_id, str(self.winner.pk))
        self.assertEqual(log.metadata['loser_ids'], [self.loser.pk])
        self.assertEqual(log.metadata['rentals_moved'], 1)
        self.assertIn('Grupos processados: 1', output)

    def test_apply_is_idempotent(self):
        self.run_command(self.default_groups(), '--apply')
        output = self.run_command(self.default_groups(), '--apply')

        self.assertIn('já mesclados: 1', output)
        self.assertEqual(
            AuditLog.objects.filter(action='merge_duplicate_customer').count(), 1,
        )

    def test_tier_and_exclusion_filters(self):
        groups = [
            _group('11111111111', [self.winner.pk, self.loser.pk], self.winner.pk, tier='T4'),
        ]
        output = self.run_command(groups, '--apply')
        self.assertIn('Grupos no escopo: 0', output)

        output = self.run_command(
            self.default_groups(), '--apply', '--exclude-cpfs=11111111111',
        )
        self.assertIn('Grupos no escopo: 0', output)
        self.loser.refresh_from_db()
        self.assertTrue(self.loser.is_active)

    def test_group_with_divergent_cpf_is_skipped(self):
        stranger = Customer.objects.create(name='Outra Pessoa', cpf='222.222.222-22')
        groups = [_group('11111111111', [self.winner.pk, stranger.pk], self.winner.pk)]

        output = self.run_command(groups, '--apply')

        stranger.refresh_from_db()
        self.assertTrue(stranger.is_active)
        self.assertIn('inválidos pulados: 1', output)
        self.assertFalse(AuditLog.objects.exists())
