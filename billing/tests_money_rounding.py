from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from billing.services import (
    _normalize_money,
    _quantize_money,
    compute_cancellation_penalty,
    compute_damage_penalty,
    compute_interest,
    compute_loss_penalty,
    compute_monthly_interest,
    compute_moratoria,
    create_penalty_receivable,
    generate_for_rental,
    total_with_interest,
)
from billing.models import Receivable
from company.models import Company
from customers.models import Customer
from rentals.models import Rental


class MoneyRoundingPolicyTests(TestCase):
    def setUp(self):
        self.company = Company.load()
        self.company.daily_interest_rate = Decimal('0.50')
        self.company.monthly_interest_rate = Decimal('0')
        self.company.late_fee_rate = Decimal('0.50')
        self.company.damage_penalty_rate = Decimal('0.50')
        self.company.loss_penalty_rate = Decimal('0.50')
        self.company.cancellation_penalty_rate = Decimal('0.50')
        self.company.save(update_fields=[
            'daily_interest_rate',
            'monthly_interest_rate',
            'late_fee_rate',
            'damage_penalty_rate',
            'loss_penalty_rate',
            'cancellation_penalty_rate',
            'updated_at',
        ])
        customer = Customer.objects.create(name='Cliente Arredondamento')
        self.rental = Rental.objects.create(
            number=98001,
            customer=customer,
            pickup_date=date(2027, 1, 10),
            return_date=date(2027, 1, 15),
            total_value=Decimal('1.00'),
        )
        self.receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=date.today() - timedelta(days=1),
            amount=Decimal('1.00'),
        )

    def test_interest_and_moratoria_use_half_up_for_half_cent(self):
        self.assertEqual(
            compute_interest(
                self.receivable,
                on_date=date.today(),
                company=self.company,
            ),
            Decimal('0.01'),
        )
        self.assertEqual(
            compute_monthly_interest(
                self.receivable,
                on_date=date.today(),
                company=self.company,
            ),
            Decimal('0.01'),
        )
        self.assertEqual(
            compute_moratoria(
                self.receivable,
                on_date=date.today(),
                company=self.company,
            ),
            Decimal('0.01'),
        )

    def test_percentage_penalties_use_half_up_for_half_cent(self):
        self.assertEqual(
            compute_damage_penalty(Decimal('1.00'), company=self.company),
            Decimal('0.01'),
        )
        self.assertEqual(
            compute_loss_penalty(Decimal('1.00'), company=self.company),
            Decimal('0.01'),
        )
        self.assertEqual(
            compute_cancellation_penalty(self.rental, company=self.company),
            Decimal('0.01'),
        )

    def test_total_with_interest_matches_the_rounded_interest(self):
        interest = compute_interest(
            self.receivable, on_date=date.today(), company=self.company,
        )

        self.assertEqual(
            total_with_interest(
                self.receivable, on_date=date.today(), company=self.company,
            ),
            self.receivable.balance + interest,
        )

    def test_received_and_charged_amounts_round_the_same_way(self):
        """The bug this policy closes: charging 0.00 while receiving 0.01."""
        self.assertEqual(_quantize_money(Decimal('0.005')), Decimal('0.01'))
        self.assertEqual(_normalize_money('0.005', 'valor'), Decimal('0.01'))
        self.assertEqual(_quantize_money(Decimal('0.015')), Decimal('0.02'))
        self.assertEqual(_normalize_money('0.015', 'valor'), Decimal('0.02'))

    def test_penalty_receivable_rounds_half_up(self):
        receivable = create_penalty_receivable(
            self.rental,
            amount=Decimal('0.005'),
            due_date=date(2027, 1, 10),
            kind='damage',
        )

        self.assertEqual(receivable.amount, Decimal('0.01'))

    def test_installment_split_rounds_half_up_and_still_sums_to_the_total(self):
        created = generate_for_rental(
            self.rental,
            installments=2,
            first_due_date=date(2026, 12, 10),
            total_amount=Decimal('0.05'),
        )

        amounts = [item.amount for item in created]
        # Half-cent base (0.025) rounds up to 0.03; the remainder lands on the
        # first installment, so the split still adds up exactly.
        self.assertEqual(amounts, [Decimal('0.02'), Decimal('0.03')])
        self.assertEqual(sum(amounts, Decimal('0')), Decimal('0.05'))
