from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from company.models import Company
from customers.models import Customer
from movements.models import Pickup, Return
from rentals.models import Rental

User = get_user_model()


class AdminForceDeleteTests(TestCase):
    """Tests for force-deleting any rental with admin password override."""

    def setUp(self):
        self.admin_password = 'SuperAdminPass123!'
        self.admin_user = User.objects.create_superuser(
            email='admin-force@noivasecia.test',
            password=self.admin_password,
        )
        self.operator = User.objects.create_user(
            email='operator@noivasecia.test',
            password='OperatorPass123!',
            is_staff=True,
        )
        ModulePermission.objects.create(user=self.operator, module_key='rentals', allowed=True)
        ActionPermission.objects.create(user=self.operator, action_key='rentals.delete', allowed=True)

        self.customer = Customer.objects.create(name='Cliente Teste')
        self.account = CashAccount.objects.create(name='Caixa Geral')

    def _create_rental_with_history(self, number, status=Rental.Status.PICKED_UP):
        """Create a rental with pickup, receivable, payment, and financial movement."""
        rental = Rental.objects.create(
            number=number,
            customer=self.customer,
            pickup_date=date(2026, 8, 1),
            return_date=date(2026, 8, 5),
            total_value=Decimal('500.00'),
            status=status,
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
        FinancialMovement.objects.create(
            date=date(2026, 8, 1),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('500.00'),
            rental=rental,
            receivable=rcv,
            payment=pmt,
        )
        return rental

    def test_force_delete_with_valid_admin_password_purges_everything(self):
        """Admin password override deletes the rental and all associated records."""
        rental = self._create_rental_with_history(9901)
        rental_pk = rental.pk

        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('rentals:delete', args=[rental_pk]),
            {'admin_password': self.admin_password},
            follow=True,
        )

        self.assertRedirects(response, reverse('rentals:list'))
        self.assertFalse(Rental.objects.filter(pk=rental_pk).exists())
        self.assertFalse(Receivable.objects.filter(rental_id=rental_pk).exists())
        self.assertFalse(FinancialMovement.objects.filter(rental_id=rental_pk).exists())
        self.assertContains(response, 'excluída com autorização administrativa')

    def test_force_delete_with_wrong_password_is_rejected(self):
        """An incorrect admin password keeps the rental intact."""
        rental = self._create_rental_with_history(9902)

        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('rentals:delete', args=[rental.pk]),
            {'admin_password': 'SenhaErrada!'},
            follow=True,
        )

        self.assertTrue(Rental.objects.filter(pk=rental.pk).exists())
        self.assertContains(response, 'Senha de administrador incorreta.')

    def test_standard_delete_without_password_still_blocks_history(self):
        """Without admin password, the standard deletion rules still apply."""
        rental = self._create_rental_with_history(9903, status=Rental.Status.PENDING)

        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('rentals:delete', args=[rental.pk]),
            {},  # no admin_password
            follow=True,
        )

        self.assertTrue(Rental.objects.filter(pk=rental.pk).exists())

    def test_standard_delete_of_cancelled_without_history_still_works(self):
        """The original flow for a clean cancelled rental remains functional."""
        rental = Rental.objects.create(
            number=9904,
            customer=self.customer,
            pickup_date=date(2026, 8, 1),
            return_date=date(2026, 8, 5),
            total_value=Decimal('200.00'),
            status=Rental.Status.CANCELLED,
        )

        self.client.force_login(self.operator)
        response = self.client.post(
            reverse('rentals:delete', args=[rental.pk]),
            {},  # no admin_password — standard path
            follow=True,
        )

        self.assertRedirects(response, reverse('rentals:list'))
        self.assertFalse(Rental.objects.filter(pk=rental.pk).exists())

    def test_force_delete_works_for_any_status(self):
        """Admin override deletes pending, picked-up, or returned rentals alike."""
        for i, status in enumerate([Rental.Status.PENDING, Rental.Status.PICKED_UP, Rental.Status.RETURNED]):
            rental = self._create_rental_with_history(9910 + i, status=status)

            self.client.force_login(self.operator)
            response = self.client.post(
                reverse('rentals:delete', args=[rental.pk]),
                {'admin_password': self.admin_password},
                follow=True,
            )

            self.assertFalse(
                Rental.objects.filter(pk=rental.pk).exists(),
                f'Rental with status {status} was not deleted',
            )

    def test_force_delete_reclaims_last_rental_number(self):
        """Force deleting the most recent rental decrements Company.last_rental_number for reuse."""
        company = Company.objects.create(name='Company Test', last_rental_number=100)
        rental = self._create_rental_with_history(100)

        self.client.force_login(self.operator)
        self.client.post(
            reverse('rentals:delete', args=[rental.pk]),
            {'admin_password': self.admin_password},
            follow=True,
        )

        company.refresh_from_db()
        self.assertEqual(company.last_rental_number, 99)
