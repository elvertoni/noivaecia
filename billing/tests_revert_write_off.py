from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ActionPermission, ModulePermission
from billing.models import Receivable
from billing.services import revert_write_off_receivable, write_off_receivable
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()


class RevertWriteOffTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='op@noivascia.com.br',
            password='pass',
            first_name='Operator',
        )
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)

        self.customer = Customer.objects.create(name='Maria Silva')
        self.rental = Rental.objects.create(
            number=99001,
            customer=self.customer,
            pickup_date=timezone.localdate(),
            return_date=timezone.localdate(),
            total_value=Decimal('500.00'),
        )
        self.receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=timezone.localdate(),
            amount=Decimal('300.00'),
            paid_amount=Decimal('0.00'),
            balance=Decimal('300.00'),
        )
        write_off_receivable(self.receivable, 'Ajuste de teste', user=self.user)
        self.receivable.refresh_from_db()

    def test_revert_write_off_service_restores_balance_and_audits(self):
        self.assertTrue(self.receivable.is_written_off)
        self.assertEqual(self.receivable.balance, Decimal('0.00'))

        success = revert_write_off_receivable(self.receivable, user=self.user, reason='Solicitado pelo cliente')
        self.assertTrue(success)

        self.receivable.refresh_from_db()
        self.assertFalse(self.receivable.is_written_off)
        self.assertIsNone(self.receivable.written_off_at)
        self.assertEqual(self.receivable.written_off_reason, '')
        self.assertEqual(self.receivable.balance, Decimal('300.00'))

        log = AuditLog.objects.filter(action='revert_write_off_receivable').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.object_id, str(self.receivable.pk))

    def test_revert_write_off_service_raises_error_if_not_written_off(self):
        revert_write_off_receivable(self.receivable, user=self.user)
        self.receivable.refresh_from_db()

        with self.assertRaises(ValueError):
            revert_write_off_receivable(self.receivable, user=self.user)

    def test_reopen_view_requires_action_permission(self):
        self.client.login(email='op@noivascia.com.br', password='pass')
        url = reverse('billing:reopen', kwargs={'pk': self.receivable.pk})

        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)

        ActionPermission.objects.create(user=self.user, action_key='billing.reopen', allowed=True)
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)

        self.receivable.refresh_from_db()
        self.assertFalse(self.receivable.is_written_off)
        self.assertEqual(self.receivable.balance, Decimal('300.00'))
