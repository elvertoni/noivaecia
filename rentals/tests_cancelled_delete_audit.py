from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission, ActionPermission
from billing.models import CashAccount, Payment, Receivable
from company.models import Company
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()


class RentalCancelledDeleteAuditTests(TestCase):
    """Tests for message clarity when attempting to delete cancelled or paid rentals."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='admin-delete@noivasecia.test',
            password='Senha12345',
            is_staff=True,
            is_superuser=True,
        )
        ModulePermission.objects.create(user=self.user, module_key='rentals', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='rentals.delete', allowed=True)
        self.client.force_login(self.user)
        Company.load()

        self.customer = Customer.objects.create(name='Celso Tavares')
        self.account = CashAccount.objects.create(name='Caixa Geral')

    def test_delete_cancelled_rental_with_payments_shows_audit_explanation(self):
        rental = Rental.objects.create(
            number=56539,
            customer=self.customer,
            pickup_date=date(2026, 8, 8),
            return_date=date(2026, 8, 10),
            total_value=Decimal('130.00'),
            status=Rental.Status.CANCELLED,
            cancelled_reason='Desistência do cliente',
        )
        receivable = Receivable.objects.create(
            rental=rental,
            due_date=date(2026, 8, 8),
            amount=Decimal('130.00'),
            paid_amount=Decimal('130.00'),
            balance=Decimal('0.00'),
        )
        Payment.objects.create(
            receivable=receivable,
            rental=rental,
            customer=self.customer,
            payment_date=date(2026, 8, 4),
            amount=Decimal('130.00'),
            method=Payment.Method.CARD_CREDIT,
        )

        response = self.client.post(
            reverse('rentals:delete', args=[rental.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Esta locação já está cancelada, mas não pode ser excluída fisicamente', content)
        self.assertNotIn('Use o cancelamento.', content)
        self.assertTrue(Rental.objects.filter(pk=rental.pk).exists())
