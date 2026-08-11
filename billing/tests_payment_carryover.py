"""Paying more than one installment asks for spills into the next ones (RF-21)."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ActionPermission, ModulePermission
from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from billing.services import (
    PaymentPlanError,
    compute_interest,
    register_payment_with_carryover,
    reprocess_future_installments,
    total_with_interest,
)
from company.models import Company
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

    def test_partial_carryover_title_does_not_deadlock_reprocessing(self):
        rental = Rental.objects.create(
            number=56582,
            customer=self.customer,
            pickup_date=self.today + timedelta(days=90),
            return_date=self.today + timedelta(days=95),
            total_value=Decimal('300.00'),
        )
        installments = [
            Receivable.objects.create(
                rental=rental,
                due_date=self.today + timedelta(days=10 * (index + 1)),
                amount=Decimal('60.00'),
            )
            for index in range(5)
        ]

        # R$ 200 against 5 x R$ 60 settles three titles and leaves the fourth
        # partially paid -- the exact state that used to freeze the schedule
        # forever under ENFORCED_V1.
        payments = register_payment_with_carryover(
            installments[0],
            Decimal('200.00'),
            self.today,
            user=self.user,
        )
        self.assertEqual(len(payments), 4)

        result = reprocess_future_installments(
            rental,
            installments=1,
            first_due_date=rental.pickup_date,
        )

        # The partially paid title is closed at what was actually received.
        partial = Receivable.objects.get(pk=installments[3].pk)
        self.assertEqual(partial.amount, Decimal('20.00'))
        self.assertEqual(partial.paid_amount, Decimal('20.00'))
        self.assertEqual(partial.balance, Decimal('0.00'))

        # Its open R$ 40 plus the untouched R$ 60 become the new schedule.
        self.assertEqual(len(result['created']), 1)
        self.assertEqual(result['created'][0].amount, Decimal('100.00'))
        self.assertEqual(result['released_amount'], Decimal('40.00'))
        self.assertEqual(
            [item['id'] for item in result['adjusted_partials']],
            [installments[3].pk],
        )

        # Invariant: the schedule still sums to the rental value.
        self.assertEqual(
            sum(
                (item.amount for item in rental.receivables.all()),
                Decimal('0'),
            ),
            Decimal('300.00'),
        )
        # Invariant: no payment lost, duplicated or detached, and the cash
        # posted still matches what the titles record as settled.
        self.assertEqual(Payment.objects.filter(rental=rental).count(), 4)
        self.assertEqual(
            Payment.objects.filter(rental=rental).aggregate(
                total=Sum('amount'))['total'],
            Decimal('200.00'),
        )
        self.assertEqual(
            sum(
                (item.paid_amount for item in rental.receivables.all()),
                Decimal('0'),
            ),
            Decimal('200.00'),
        )
        self.assertFalse(Receivable.objects.filter(balance__lt=0).exists())

        # And the schedule can be reorganized again afterwards. Without an
        # explicit first due date the golden rule anchors the last installment
        # on the pickup date, so both new titles stay under the ceiling.
        again = reprocess_future_installments(
            rental,
            installments=2,
        )
        self.assertEqual(len(again['created']), 2)
        self.assertEqual(
            sum(
                (item.amount for item in rental.receivables.all()),
                Decimal('0'),
            ),
            Decimal('300.00'),
        )

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

    def test_full_principal_plus_interest_never_reduces_principal_twice(self):
        overdue = Receivable.objects.create(
            rental=self.rental,
            due_date=self.today - timedelta(days=60),
            amount=Decimal('60.00'),
        )
        interest = total_with_interest(overdue) - overdue.balance
        amount = Decimal('360.00') + interest

        payments = register_payment_with_carryover(
            receivable=overdue,
            amount=amount,
            payment_date=self.today,
            user=self.user,
        )

        overdue.refresh_from_db()
        self.assertEqual(overdue.balance, Decimal('0.00'))
        self.assertEqual(overdue.paid_amount, Decimal('60.00'))
        self.assertEqual(payments[-1].interest_amount, interest)
        self.assertEqual(
            FinancialMovement.objects.aggregate(total=Sum('amount'))['total'],
            amount,
        )
        self.assertFalse(Receivable.objects.filter(balance__lt=0).exists())

    def test_cascade_settles_other_overdue_titles_without_charging_interest(self):
        """Deliberate policy: only the opened title accrues late interest.

        Intermediate installment dates are an expectation, not an obligation,
        so a title the cascade merely passes through is settled at its plain
        balance.
        """
        company = Company.load()
        company.monthly_interest_rate = Decimal('3.00')
        company.daily_interest_rate = Decimal('0')
        company.save(update_fields=[
            'monthly_interest_rate', 'daily_interest_rate', 'updated_at',
        ])
        overdue = Receivable.objects.create(
            rental=self.rental,
            due_date=self.today - timedelta(days=60),
            amount=Decimal('60.00'),
        )
        overdue_interest = compute_interest(overdue, on_date=self.today, company=company)
        self.assertGreater(overdue_interest, Decimal('0'))

        # The cashier opens a title that carries no interest of its own, so the
        # ceiling is pure principal: 60 (overdue) + 5 x 60 (future).
        payments = register_payment_with_carryover(
            self.installments[0],
            Decimal('360.00'),
            self.today,
            user=self.user,
        )

        overdue.refresh_from_db()
        self.assertEqual(overdue.balance, Decimal('0.00'))
        self.assertEqual(overdue.paid_amount, Decimal('60.00'))
        self.assertEqual(
            sum((payment.interest_amount for payment in payments), Decimal('0')),
            Decimal('0.00'),
        )
        self.assertFalse(Receivable.objects.filter(balance__lt=0).exists())

    def test_cascade_interest_is_not_available_as_extra_capacity(self):
        """A surplus beyond principal is refused, not silently booked as interest."""
        company = Company.load()
        company.monthly_interest_rate = Decimal('3.00')
        company.daily_interest_rate = Decimal('0')
        company.save(update_fields=[
            'monthly_interest_rate', 'daily_interest_rate', 'updated_at',
        ])
        overdue = Receivable.objects.create(
            rental=self.rental,
            due_date=self.today - timedelta(days=60),
            amount=Decimal('60.00'),
        )
        overdue_interest = compute_interest(overdue, on_date=self.today, company=company)

        with self.assertRaises(PaymentPlanError) as ctx:
            register_payment_with_carryover(
                self.installments[0],
                Decimal('360.00') + overdue_interest,
                self.today,
                user=self.user,
            )

        message = str(ctx.exception)
        self.assertIn('maior que o saldo total desta locação', message)
        # The cap is stated as principal only, and the cashier is told why.
        self.assertIn('R$ 360.00 de saldo em aberto', message)
        self.assertIn('sem juros neste recebimento', message)
        self.assertEqual(Payment.objects.count(), 0)

    def test_opened_overdue_title_still_accrues_its_own_interest(self):
        """The pre-existing interest branch must keep working untouched."""
        overdue = Receivable.objects.create(
            rental=self.rental,
            due_date=self.today - timedelta(days=60),
            amount=Decimal('60.00'),
        )
        interest = total_with_interest(overdue) - overdue.balance
        self.assertGreater(interest, Decimal('0'))

        payments = register_payment_with_carryover(
            overdue,
            Decimal('360.00') + interest,
            self.today,
            user=self.user,
        )

        overdue.refresh_from_db()
        # Interest is charged, and it never eats into the principal.
        self.assertEqual(overdue.paid_amount, Decimal('60.00'))
        self.assertEqual(overdue.balance, Decimal('0.00'))
        self.assertEqual(payments[-1].interest_amount, interest)
        self.assertEqual(
            sum((payment.interest_amount for payment in payments), Decimal('0')),
            interest,
        )

    def test_written_off_target_is_rejected(self):
        target = self.installments[0]
        target.written_off_at = timezone.now()
        target.written_off_reason = 'Teste'
        target.save()

        with self.assertRaisesMessage(ValueError, 'Não é possível receber um título baixado.'):
            register_payment_with_carryover(target, Decimal('10.00'), self.today)

        self.assertFalse(Payment.objects.exists())

    def test_negative_or_paid_target_is_rejected(self):
        target = self.installments[0]
        target.paid_amount = Decimal('60.01')
        target.save(update_fields=['paid_amount', 'balance', 'updated_at'])

        with self.assertRaisesMessage(
            ValueError,
            'Não é possível receber um título quitado ou com saldo inválido.',
        ):
            register_payment_with_carryover(target, Decimal('10.00'), self.today)

        self.assertFalse(Payment.objects.exists())

    def test_service_quantizes_received_value_to_cents(self):
        payments = register_payment_with_carryover(
            self.installments[0],
            Decimal('0.005'),
            self.today,
        )

        self.assertEqual(payments[0].amount, Decimal('0.01'))

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
