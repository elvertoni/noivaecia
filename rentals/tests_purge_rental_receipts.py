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
from core.models import AuditLog
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

    def test_force_delete_purges_the_whole_receipt_chain(self):
        """The admin override exists to erase usability-test data, so it has to
        reach the receipts too — PROTECT must not turn it into a dead end."""
        rental, receipt = self._create_rental_with_receipt(9950)
        rental_pk = rental.pk

        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('rentals:delete', args=[rental_pk]),
            {'admin_password': self.admin_password},
            follow=True,
        )

        self.assertRedirects(response, reverse('rentals:list'))

        # Nothing of the test rental survives, and no ProtectedError leaked.
        self.assertFalse(Rental.objects.filter(pk=rental_pk).exists())
        self.assertFalse(Receivable.objects.filter(rental_id=rental_pk).exists())
        self.assertFalse(FinancialMovement.objects.filter(rental_id=rental_pk).exists())
        self.assertFalse(Receipt.objects.filter(pk=receipt.pk).exists())
        self.assertFalse(ReceiptAllocation.objects.filter(receipt_id=receipt.pk).exists())

        # What was destroyed is recorded, since this bypasses audit retention.
        entry = AuditLog.objects.filter(action='rental_force_delete').latest('id')
        self.assertIn('recibo', entry.reason)

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

    def test_direct_purge_helper_removes_receipt_before_its_movement(self):
        """Unit-level check on the helper itself, independent of the view.

        Ordering matters: ``Receipt.financial_movement`` is PROTECT, so the
        receipt has to go before the movement it points at.
        """
        from rentals.views import RentalDeleteView

        rental, receipt = self._create_rental_with_receipt(9952)

        RentalDeleteView._purge_rental(rental, self.admin_user)

        self.assertFalse(Rental.objects.filter(pk=rental.pk).exists())
        self.assertFalse(Receipt.objects.filter(pk=receipt.pk).exists())
        self.assertFalse(ReceiptAllocation.objects.filter(receipt_id=receipt.pk).exists())
