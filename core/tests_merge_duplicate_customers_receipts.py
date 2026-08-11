"""Regression coverage: merge_duplicate_customers must reassign
billing.Receipt.customer, which is on_delete=PROTECT.

Before this fix, only Rental/Payment/FinancialMovement/CustomerMessage were
moved to the winner. Any future cleanup that deletes a deactivated loser
customer would hit ProtectedError as soon as that loser had a Receipt on
file. This test proves the merge command now reassigns the receipt too, and
that reassigning it (then deleting the loser directly) no longer raises
ProtectedError.
"""

import json
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from billing.models import CashAccount, FinancialMovement, Payment, Receipt, Receivable
from customers.models import Customer
from rentals.models import Rental


def _group(cpf, ids, winner, tier='T1'):
    return {
        'cpf': cpf,
        'tier': tier,
        'ids': [{'id': pk} for pk in ids],
        'winner_suggestion': winner,
    }


class MergeDuplicateCustomersReceiptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.winner = Customer.objects.create(
            name='Joana Pereira', cpf='222.222.222-22', city='Bandeirantes',
        )
        cls.loser = Customer.objects.create(
            name='Joana P.', cpf='22222222222', city='Bandeirantes',
        )
        cls.rental = Rental.objects.create(
            number=501, customer=cls.loser,
            pickup_date=date(2020, 2, 10), return_date=date(2020, 2, 12),
        )
        receivable = Receivable.objects.create(
            rental=cls.rental, due_date=date(2020, 2, 15),
            amount=Decimal('300'), balance=Decimal('300'),
        )
        cls.payment = Payment.objects.create(
            receivable=receivable, customer=cls.loser,
            payment_date=date(2020, 2, 15), amount=Decimal('300'),
        )
        account = CashAccount.objects.create(name='Caixa')
        movement = FinancialMovement.objects.create(
            date=date(2020, 2, 15), account=account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('300'), customer=cls.loser,
        )
        cls.receipt = Receipt.objects.create(
            customer=cls.loser,
            received_on=date(2020, 2, 15),
            amount=Decimal('300'),
            method=Payment.Method.CASH,
            payload_hash='cafef00d',
            financial_movement=movement,
        )

    def run_command(self, groups, *args):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / 'tiers.json'
            json_path.write_text(json.dumps(groups), encoding='utf-8')
            stdout = StringIO()
            call_command('merge_duplicate_customers', str(json_path), *args, stdout=stdout)
        return stdout.getvalue()

    def default_groups(self):
        return [_group('22222222222', [self.winner.pk, self.loser.pk], self.winner.pk)]

    def test_apply_reassigns_receipt_customer_to_winner(self):
        output = self.run_command(self.default_groups(), '--apply')

        self.receipt.refresh_from_db()
        self.loser.refresh_from_db()

        self.assertEqual(self.receipt.customer_id, self.winner.pk)
        self.assertFalse(self.loser.is_active)
        self.assertIn('recibos: 1', output)

    def test_loser_can_be_deleted_after_merge_without_protected_error(self):
        """Proves the fix: once the receipt moved off the loser, the loser
        no longer has any PROTECT-guarded row pointing at it and can be
        deleted outright (the command itself never deletes losers, but this
        confirms no dangling protected reference was left behind)."""
        self.run_command(self.default_groups(), '--apply')

        self.loser.refresh_from_db()
        # Would raise django.db.models.ProtectedError before this fix,
        # because Receipt.customer still pointed at the loser.
        self.loser.delete()

        self.assertFalse(Customer.objects.filter(pk=self.loser.pk).exists())
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.customer_id, self.winner.pk)

    def test_dry_run_does_not_move_receipt(self):
        self.run_command(self.default_groups())

        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.customer_id, self.loser.pk)
