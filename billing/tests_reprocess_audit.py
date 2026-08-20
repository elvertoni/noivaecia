import uuid
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db.models import ProtectedError
from django.test import TestCase

from billing.models import CashAccount, Payment, Receivable, ReceiptAllocation
from billing.services import (
    preview_future_reprocess,
    register_receipt,
    reprocess_future_installments,
)
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental


class ReprocessAuditCommandTests(TestCase):
    """The command has to tell churn apart from a real change to the plan."""

    def setUp(self):
        self.customer = Customer.objects.create(name='Ana Souza')
        self.rental = Rental.objects.create(
            number=2001,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            total_value=Decimal('300.00'),
        )

    def _run(self, *args):
        out = StringIO()
        call_command('reprocess_audit', *args, stdout=out)
        return out.getvalue()

    def test_no_events_reports_clean(self):
        self.assertIn('Total de eventos: 0', self._run())

    def test_preview_matches_what_the_rewrite_actually_reschedules(self):
        """Shaped after production rental #56463, which has a partial title.

        The preview must close the partial title at the amount already received
        before working out the leftover, exactly as the rewrite does — otherwise
        the confirmation dialog quotes a figure the operation will not produce.
        """
        self.rental.total_value = Decimal('560.00')
        self.rental.save(update_fields=['total_value'])

        settled = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 3, 20), amount=Decimal('150.00')
        )
        Payment.objects.create(
            receivable=settled, payment_date=date(2026, 3, 20), amount=Decimal('150.00')
        )
        settled.recalculate_from_payments()

        partial = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 4, 18), amount=Decimal('390.00')
        )
        Payment.objects.create(
            receivable=partial, payment_date=date(2026, 4, 18), amount=Decimal('200.00')
        )
        partial.recalculate_from_payments()

        preview = preview_future_reprocess(self.rental)
        self.assertTrue(preview['can_reprocess'])
        self.assertEqual(preview['remaining'], Decimal('210.00'))
        self.assertEqual(preview['partial_count'], 1)
        self.assertEqual(preview['partial_released'], Decimal('190.00'))

        result = reprocess_future_installments(
            self.rental, installments=1, first_due_date=date(2026, 5, 21)
        )
        self.assertEqual(result['scheduled_amount'], preview['remaining'])
        self.assertEqual(
            sum(item.amount for item in result['created']), preview['remaining']
        )

    def test_registered_receipt_survives_the_rewrite(self):
        """The reported fear: can reorganizing destroy money already received?"""
        paid = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 4, 10), amount=Decimal('100.00')
        )
        payment = Payment.objects.create(
            receivable=paid, payment_date=date(2026, 4, 10), amount=Decimal('100.00')
        )
        paid.recalculate_from_payments()
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 5, 10), amount=Decimal('200.00')
        )

        reprocess_future_installments(
            self.rental, installments=2, first_due_date=date(2026, 5, 1)
        )

        # The settled title and its payment are untouched.
        paid.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(paid.paid_amount, Decimal('100.00'))
        self.assertEqual(payment.amount, Decimal('100.00'))
        self.assertEqual(payment.receivable_id, paid.pk)

        output = self._run()
        self.assertIn('nenhum título com valor recebido foi apagado', output)
        self.assertIn('nenhum movimento de caixa ficou sem título', output)

    def test_database_refuses_to_delete_a_title_tied_to_a_receipt(self):
        """Last barrier: even a broken filter cannot destroy a receipt.

        ``ReceiptAllocation.receivable`` is PROTECT, so the bulk delete inside
        ``reprocess_future_installments`` raises and the whole atomic block
        rolls back instead of writing a mutilated schedule.
        """
        account = CashAccount.objects.create(name='Caixa teste')
        title = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 4, 10), amount=Decimal('100.00')
        )
        register_receipt(
            idempotency_key=uuid.uuid4(),
            payload={
                'rental_id': self.rental.pk,
                'cash_account_id': account.pk,
                'received_on': date(2026, 4, 10),
                'amount': Decimal('100.00'),
                'method': 'cash',
                'notes': '',
                'allocations': [{
                    'receivable_id': title.pk,
                    'cash_amount': Decimal('100.00'),
                    'interest_amount': Decimal('0'),
                    'discount_amount': Decimal('0'),
                }],
            },
        )
        self.assertTrue(ReceiptAllocation.objects.filter(receivable=title).exists())

        with self.assertRaises(ProtectedError):
            Receivable.objects.filter(pk=title.pk).delete()

        self.assertTrue(Receivable.objects.filter(pk=title.pk).exists())

    def test_audit_flags_a_deleted_title_that_carried_money(self):
        """Guard against a future regression in the protection filter."""
        AuditLog.objects.create(
            action='reprocess_future_installments',
            model_name='Rental',
            object_id=str(self.rental.pk),
            metadata={
                'deleted_receivable_ids': [999],
                'previous_schedule': [{
                    'id': 999,
                    'due_date': '2026-05-10',
                    'amount': '100.00',
                    'paid_amount': '40.00',
                    'balance': '60.00',
                }],
                'adjusted_partials': [],
            },
        )

        output = self._run()
        self.assertIn('ALERTA', output)
        self.assertIn('apagado com R$ 40.00 recebido', output)

    def test_partial_split_on_same_due_date_is_only_value_rewritten(self):
        """Fragmenting a title on its date must not invent a date change."""
        AuditLog.objects.create(
            action='reprocess_future_installments',
            model_name='Rental',
            object_id=str(self.rental.pk),
            metadata={
                'previous_schedule': [{
                    'id': 901,
                    'due_date': '2026-05-10',
                    'amount': '200.00',
                    'paid_amount': '50.00',
                    'balance': '150.00',
                }],
                'protected_receivable_ids': [901],
                'adjusted_partials': [{
                    'id': 901,
                    'due_date': '2026-05-10',
                    'previous_amount': '200.00',
                    'amount': '50.00',
                    'released_amount': '150.00',
                }],
                'deleted_receivable_ids': [],
                'new_schedule': [{
                    'id': 902,
                    'due_date': '2026-05-10',
                    'amount': '150.00',
                }],
            },
        )

        output = self._run()

        self.assertIn('valor-reescrito: 1', output)
        self.assertIn('vencimento-e-valor: 0', output)
        self.assertIn('sem reduzir o total por vencimento', output)

    def test_multi_title_receipt_movement_is_not_reported_as_orphan(self):
        account = CashAccount.objects.create(name='Caixa multi-título')
        first = Receivable.objects.create(
            rental=self.rental,
            due_date=date(2026, 4, 10),
            amount=Decimal('150.00'),
        )
        second = Receivable.objects.create(
            rental=self.rental,
            due_date=date(2026, 5, 10),
            amount=Decimal('150.00'),
        )
        receipt = register_receipt(
            idempotency_key=uuid.uuid4(),
            payload={
                'rental_id': self.rental.pk,
                'cash_account_id': account.pk,
                'received_on': date(2026, 4, 10),
                'amount': Decimal('100.00'),
                'method': 'cash',
                'notes': '',
                'allocations': [
                    {
                        'receivable_id': first.pk,
                        'cash_amount': Decimal('50.00'),
                        'interest_amount': Decimal('0'),
                        'discount_amount': Decimal('0'),
                    },
                    {
                        'receivable_id': second.pk,
                        'cash_amount': Decimal('50.00'),
                        'interest_amount': Decimal('0'),
                        'discount_amount': Decimal('0'),
                    },
                ],
            },
        )
        reprocess_future_installments(
            self.rental,
            installments=1,
            first_due_date=date(2026, 6, 1),
        )

        movement = receipt.financial_movement
        self.assertIsNone(movement.receivable_id)
        self.assertEqual(movement.receipt, receipt)
        self.assertIn(
            'nenhum movimento de caixa ficou sem título vinculado',
            self._run(),
        )

    def test_rewrite_that_moves_a_due_date_is_flagged(self):
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 5, 10), amount=Decimal('300.00')
        )

        reprocess_future_installments(
            self.rental, installments=3, first_due_date=date(2026, 4, 1)
        )

        output = self._run()
        self.assertIn('Total de eventos: 1', output)
        self.assertIn('vencimento-alterado: 1', output)
        self.assertIn('alteraram o plano combinado', output)

    def test_scheduling_unscheduled_balance_is_not_treated_as_damage(self):
        """The panel's legitimate use: the rental had balance with no title.

        Nothing the customer already agreed to disappears, so this must not be
        reported alongside the rewrites that moved a due date.
        """
        settled = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 4, 10), amount=Decimal('100.00')
        )
        Payment.objects.create(
            receivable=settled, payment_date=date(2026, 4, 10), amount=Decimal('100.00')
        )
        settled.recalculate_from_payments()

        reprocess_future_installments(
            self.rental, installments=1, first_due_date=date(2026, 5, 1)
        )

        output = self._run()
        self.assertIn('parcela-adicionada: 1', output)
        self.assertIn('sem mexer no que já existia', output)
        self.assertIn('Nenhum plano de parcelas foi alterado', output)
        self.assertNotIn('alteraram o plano combinado', output)

    def test_each_event_is_judged_against_its_own_outcome(self):
        """Two rewrites on one rental must not contaminate each other.

        Comparing an old event against the rental's *current* titles would
        blame the first rewrite for what the second one did.
        """
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 5, 10), amount=Decimal('300.00')
        )
        reprocess_future_installments(
            self.rental, installments=1, first_due_date=date(2026, 5, 10)
        )
        reprocess_future_installments(
            self.rental, installments=2, first_due_date=date(2026, 4, 1)
        )

        output = self._run()
        self.assertIn('Total de eventos: 2', output)
        self.assertIn('no-op: 1', output)
        self.assertIn('vencimento-alterado: 1', output)

    def test_rewrite_of_a_partially_paid_title_is_flagged(self):
        partial = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 4, 10), amount=Decimal('200.00')
        )
        Payment.objects.create(
            receivable=partial, payment_date=date(2026, 4, 10), amount=Decimal('50.00')
        )
        partial.recalculate_from_payments()
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 5, 10), amount=Decimal('100.00')
        )

        reprocess_future_installments(
            self.rental, installments=2, first_due_date=date(2026, 5, 1)
        )

        output = self._run()
        self.assertIn('R$ 200.00 -> R$ 50.00', output)
        self.assertIn('alteraram o plano combinado', output)
        self.assertIn('vencimento-e-valor: 1', output)
        self.assertIn('valor-reescrito: 0', output)

    def test_only_changed_hides_noop_events(self):
        """A rewrite that reproduces the same plan is churn, not damage."""
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 5, 10), amount=Decimal('300.00')
        )

        reprocess_future_installments(
            self.rental, installments=1, first_due_date=date(2026, 5, 10)
        )

        full = self._run()
        self.assertIn('no-op: 1', full)
        self.assertIn('Nenhum plano de parcelas foi alterado', full)

        filtered = self._run('--only-changed')
        self.assertIn('Total de eventos: 1', filtered)
        self.assertNotIn('locação', filtered)
