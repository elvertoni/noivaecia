"""Regression coverage for _purge_rental refusing when receipts exist.

billing.Receipt / ReceiptAllocation are on_delete=PROTECT (real cash-event
ledger rows). Before this fix, the admin force-delete override deleted the
rental's FinancialMovement rows unconditionally, which would raise
ProtectedError as soon as any Receipt existed for the rental. It should
instead refuse with a clear PT-BR message and leave everything intact.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from billing.models import (
    CashAccount, FinancialMovement, Payment, Receipt, ReceiptAllocation, Receivable,
)
from customers.models import Customer
from movements.models import Pickup
from rentals.models import Rental

User = get_user_model()


class PurgeRentalWithReceiptTests(TestCase):
    """Force-delete must refuse to purge a rental that has a Receipt on file."""

    def setUp(self):
        self.admin_password = 'SuperAdminPass123!'
        self.admin_user = User.objects.create_superuser(
            email='admin-purge-receipt@noivasecia.test',
            password=self.admin_password,
        )
        self.operator = User.objects.create_user(
            email='operator-purge-receipt@noivasecia.test',
            password='OperatorPass123!',
            is_staff=True,
        )
        ModulePermission.objects.create(user=self.operator, module_key='rentals', allowed=True)
        ActionPermission.objects.create(user=self.operator, action_key='rentals.delete', allowed=True)

        self.customer = Customer.objects.create(name='Cliente Com Recibo')
        self.account = CashAccount.objects.create(name='Caixa Geral')

    def _create_rental_with_receipt(self, number):
        rental = Rental.objects.create(
            number=number,
            customer=self.customer,
            pickup_date=date(2026, 8, 1),
            return_date=date(2026, 8, 5),
            total_value=Decimal('500.00'),
            status=Rental.Status.PICKED_UP,
        )
        Pickup.objects.create(rental=rental, pickup_date=date(2026, 8, 1))
        rcv = Receivable.objects.create(
            rental=rental,
            due_date=date(2026, 8, 1),
            amount=Decimal('500.00'),
            paid_amount=Decimal('500.00'),
            balance=Decimal('0.00'),
        )
        pmt = Payment.objects.create(
            receivable=rcv,
            customer=self.customer,
            rental=rental,
            payment_date=date(2026, 8, 1),
            amount=Decimal('500.00'),
            user=self.operator,
        )
        movement = FinancialMovement.objects.create(
            date=date(2026, 8, 1),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('500.00'),
            rental=rental,
            receivable=rcv,
            payment=pmt,
        )
        receipt = Receipt.objects.create(
            customer=self.customer,
            received_on=date(2026, 8, 1),
            amount=Decimal('500.00'),
            method=Payment.Method.CASH,
            payload_hash='deadbeef',
            financial_movement=movement,
        )
        ReceiptAllocation.objects.create(
            receipt=receipt,
            receivable=rcv,
            payment=pmt,
            cash_amount=Decimal('500.00'),
            principal_amount=Decimal('500.00'),
            interest_amount=Decimal('0.00'),
            discount_amount=Decimal('0.00'),
        )
        return rental, receipt

    def test_force_delete_with_receipt_is_refused_and_nothing_is_deleted(self):
        rental, receipt = self._create_rental_with_receipt(9950)
        rental_pk = rental.pk

        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('rentals:delete', args=[rental_pk]),
            {'admin_password': self.admin_password},
            follow=True,
        )

        # Refused, not a 500/ProtectedError leaking to the client.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'recibos')

        # Nothing was purged: the whole chain is intact.
        self.assertTrue(Rental.objects.filter(pk=rental_pk).exists())
        self.assertTrue(Receivable.objects.filter(rental_id=rental_pk).exists())
        self.assertTrue(FinancialMovement.objects.filter(rental_id=rental_pk).exists())
        self.assertTrue(Receipt.objects.filter(pk=receipt.pk).exists())
        self.assertTrue(ReceiptAllocation.objects.filter(receipt=receipt).exists())

    def test_force_delete_without_receipt_still_purges(self):
        """Sanity check: the existing admin-override purge path is untouched
        for rentals that have no receipts."""
        rental = Rental.objects.create(
            number=9951,
            customer=self.customer,
            pickup_date=date(2026, 8, 1),
            return_date=date(2026, 8, 5),
            total_value=Decimal('200.00'),
            status=Rental.Status.PENDING,
        )
        rcv = Receivable.objects.create(
            rental=rental, due_date=date(2026, 8, 1),
            amount=Decimal('200.00'), balance=Decimal('200.00'),
        )
        FinancialMovement.objects.create(
            date=date(2026, 8, 1),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('200.00'),
            rental=rental,
            receivable=rcv,
        )
        rental_pk = rental.pk

        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('rentals:delete', args=[rental_pk]),
            {'admin_password': self.admin_password},
            follow=True,
        )

        self.assertRedirects(response, reverse('rentals:list'))
        self.assertFalse(Rental.objects.filter(pk=rental_pk).exists())
        self.assertFalse(FinancialMovement.objects.filter(rental_id=rental_pk).exists())

    def test_direct_purge_helper_raises_when_receipt_exists(self):
        """Unit-level check on the helper itself, independent of the view."""
        from rentals.views import RentalDeleteView, RentalPurgeBlocked

        rental, receipt = self._create_rental_with_receipt(9952)

        with self.assertRaises(RentalPurgeBlocked):
            RentalDeleteView._purge_rental(rental, self.admin_user)

        # No ProtectedError either — the refusal happens before any delete.
        self.assertTrue(Rental.objects.filter(pk=rental.pk).exists())
