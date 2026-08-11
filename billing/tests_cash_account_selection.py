"""The cash account must not be a global serialization point (see services)."""

import inspect
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from billing.services import (
    _active_cash_account,
    _assert_cash_account_active,
    register_payment,
)
from customers.models import Customer
from rentals.models import Rental


class CashAccountSelectionTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.account = CashAccount.objects.create(name='Caixa principal')
        self.customer = Customer.objects.create(name='Cliente Caixa')
        self.rental = Rental.objects.create(
            number=77001,
            customer=self.customer,
            pickup_date=self.today + timedelta(days=30),
            return_date=self.today + timedelta(days=35),
            total_value=Decimal('100.00'),
        )
        self.receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=self.today + timedelta(days=10),
            amount=Decimal('100.00'),
        )

    def test_selection_does_not_lock_the_account_row(self):
        # Guards against silently reintroducing the lock that made every
        # payment in the system queue behind the same row. co_names holds the
        # names the compiled body actually references, so prose in the
        # docstring cannot satisfy or trip this check.
        self.assertNotIn('select_for_update', _active_cash_account.__code__.co_names)
        self.assertIn('CashAccount', _active_cash_account.__code__.co_names)
        # register_payment must reach the account only through the helper.
        self.assertNotIn('CashAccount', register_payment.__code__.co_names)
        self.assertIn('_active_cash_account', register_payment.__code__.co_names)
        self.assertIn('_assert_cash_account_active', register_payment.__code__.co_names)

    def test_selection_picks_the_first_active_account(self):
        CashAccount.objects.create(name='Caixa secundário')
        inactive = CashAccount.objects.create(name='Caixa inativo', active=False)

        selected = _active_cash_account()

        self.assertEqual(selected.pk, self.account.pk)
        self.assertNotEqual(selected.pk, inactive.pk)

    def test_selection_returns_none_when_every_account_is_inactive(self):
        CashAccount.objects.update(active=False)

        self.assertIsNone(_active_cash_account())

    def test_revalidation_rejects_a_deactivated_account(self):
        CashAccount.objects.filter(pk=self.account.pk).update(active=False)

        with self.assertRaisesMessage(ValueError, 'desativada durante o recebimento'):
            _assert_cash_account_active(self.account)

    def test_payment_rolls_back_when_the_account_is_deactivated_midway(self):
        """The revalidation replaces exactly what the removed row lock gave."""
        def select_then_deactivate():
            account = CashAccount.objects.filter(active=True).order_by('id').first()
            # Simulates a deactivation committing right after the selection.
            CashAccount.objects.filter(pk=account.pk).update(active=False)
            return account

        with mock.patch(
            'billing.services._active_cash_account', select_then_deactivate,
        ):
            with self.assertRaisesMessage(
                ValueError, 'desativada durante o recebimento',
            ):
                register_payment(
                    self.receivable, Decimal('40.00'), self.today,
                )

        self.assertFalse(Payment.objects.exists())
        self.assertFalse(FinancialMovement.objects.exists())
        self.receivable.refresh_from_db()
        self.assertEqual(self.receivable.paid_amount, Decimal('0.00'))
        self.assertEqual(self.receivable.balance, Decimal('100.00'))

    def test_payment_without_any_active_account_is_refused(self):
        CashAccount.objects.update(active=False)

        with self.assertRaisesMessage(ValueError, 'sem uma conta caixa ativa'):
            register_payment(self.receivable, Decimal('40.00'), self.today)

        self.assertFalse(Payment.objects.exists())
        self.assertFalse(FinancialMovement.objects.exists())

    def test_payment_posts_to_the_active_account(self):
        payment = register_payment(
            self.receivable, Decimal('40.00'), self.today,
        )

        movement = FinancialMovement.objects.get(payment=payment)
        self.assertEqual(movement.account_id, self.account.pk)
        self.assertEqual(movement.amount, Decimal('40.00'))
        self.receivable.refresh_from_db()
        self.assertEqual(self.receivable.balance, Decimal('60.00'))
