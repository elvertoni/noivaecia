"""Paying more than one installment asks for spills into the next ones (RF-21)."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ActionPermission, ModulePermission
from billing.models import CashAccount, Payment, Receivable
from billing.services import (
    PaymentPlanError,
    register_payment_with_carryover,
    total_with_interest,
)
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()


class PaymentCarryoverTests(TestCase):
    """Mirrors the real case: R$ 120 entrada paid, then 5 x R$ 60 monthly."""

    def setUp(self):
        self.today = timezone.localdate()
        self.user = User.objects.create_user(email='carryover@test.com', password='pass')
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='billing.receive', allowed=True)
        self.client.force_login(self.user)
        CashAccount.objects.create(name='Caixa principal')

        self.customer = Customer.objects.create(name='Elvertoni Martelli Coimbra')
        self.rental = Rental.objects.create(
            number=56580,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            total_value=Decimal('420.00'),
        )
        # Five monthly installments still to come, as in the reported case: no
        # late interest is due, so the arithmetic is purely the allocation.
        self.installments = [
            Receivable.objects.create(
                rental=self.rental,
                due_date=self.today + timedelta(days=30 * (index + 1)),
                amount=Decimal('60.00'),
            )
            for index in range(5)
        ]

    def test_surplus_settles_the_following_installments(self):
        first = self.installments[0]

        payments = register_payment_with_carryover(
            receivable=first,
            amount=Decimal('200.00'),
            payment_date=self.today,
            user=self.user,
        )

        # 60 + 60 + 60 covered fully, 20 lands on the fourth.
        self.assertEqual(len(payments), 4)
        self.assertEqual(
            [payment.amount for payment in payments],
            [Decimal('60.00'), Decimal('60.00'), Decimal('60.00'), Decimal('20.00')],
        )

        balances = [
            Receivable.objects.get(pk=item.pk).balance for item in self.installments
        ]
        self.assertEqual(balances, [
            Decimal('0.00'), Decimal('0.00'), Decimal('0.00'),
            Decimal('40.00'), Decimal('60.00'),
        ])

    def test_no_installment_is_pushed_into_a_negative_balance(self):
        register_payment_with_carryover(
            receivable=self.installments[0],
            amount=Decimal('300.00'),
            payment_date=self.today,
            user=self.user,
        )

        self.assertFalse(Receivable.objects.filter(balance__lt=0).exists())
        self.assertEqual(
            Receivable.objects.filter(rental=self.rental, balance__gt=0).count(), 0,
        )

    def test_amount_above_the_whole_rental_balance_is_refused(self):
        with self.assertRaises(PaymentPlanError) as ctx:
            register_payment_with_carryover(
                receivable=self.installments[0],
                amount=Decimal('300.01'),
                payment_date=self.today,
                user=self.user,
            )

        self.assertIn('saldo total desta locação', str(ctx.exception))
        self.assertEqual(Payment.objects.count(), 0)

    def test_surplus_never_leaks_into_another_rental(self):
        other_rental = Rental.objects.create(
            number=56581,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            total_value=Decimal('60.00'),
        )
        untouched = Receivable.objects.create(
            rental=other_rental,
            due_date=date(2026, 1, 5),
            amount=Decimal('60.00'),
        )

        register_payment_with_carryover(
            receivable=self.installments[0],
            amount=Decimal('300.00'),
            payment_date=self.today,
            user=self.user,
        )

        untouched.refresh_from_db()
        self.assertEqual(untouched.balance, Decimal('60.00'))

    def test_principal_is_settled_before_late_interest(self):
        overdue = Receivable.objects.create(
            rental=self.rental,
            due_date=self.today - timedelta(days=60),
            amount=Decimal('60.00'),
        )
        interest = total_with_interest(overdue) - overdue.balance
        self.assertGreater(interest, Decimal('0'))

        # Enough for the overdue title plus one future installment, but not for
        # the interest — the schedule must be paid down before any interest.
        register_payment_with_carryover(
            receivable=overdue,
            amount=Decimal('120.00'),
            payment_date=self.today,
            user=self.user,
        )

        overdue.refresh_from_db()
        self.assertEqual(overdue.balance, Decimal('0.00'))
        self.assertEqual(Receivable.objects.get(pk=self.installments[0].pk).balance, Decimal('0.00'))
        self.assertFalse(Receivable.objects.filter(balance__lt=0).exists())

    def test_payment_view_accepts_an_amount_above_the_single_title(self):
        response = self.client.post(
            reverse('billing:pay', args=[self.installments[0].pk]),
            {'value': '200,00', 'payment_date': self.today.strftime('%d/%m/%Y')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'distribuído entre 4 parcelas')
        self.assertEqual(Payment.objects.count(), 4)

    def test_payment_view_reports_the_rental_wide_cap_on_the_field(self):
        response = self.client.post(
            reverse('billing:pay', args=[self.installments[0].pk]),
            {'value': '500,00', 'payment_date': self.today.strftime('%d/%m/%Y')},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'maior que o saldo total desta locação')
        self.assertEqual(Payment.objects.count(), 0)
