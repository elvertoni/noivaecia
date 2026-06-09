from datetime import date
from decimal import Decimal

from django.test import TestCase

from billing import services
from billing.models import Receivable
from company.models import Company
from customers.models import Customer
from rentals.models import Rental


class ReceivableModelTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
        )

    def test_balance_derived_on_save(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20),
            amount=Decimal('200'), paid_amount=Decimal('50'),
        )
        self.assertEqual(rec.balance, Decimal('150'))

    def test_register_payment_updates_fields(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('200'),
        )
        rec.register_payment(Decimal('80'), date(2026, 6, 21))
        self.assertEqual(rec.paid_amount, Decimal('80'))
        self.assertEqual(rec.balance, Decimal('120'))
        self.assertEqual(rec.last_payment_date, date(2026, 6, 21))

    def test_is_paid_when_balance_zero(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20),
            amount=Decimal('100'), paid_amount=Decimal('100'),
        )
        self.assertTrue(rec.is_paid)


class InterestServiceTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            total_value=Decimal('300'),
        )
        company = Company.load()
        company.daily_interest_rate = Decimal('1.00')  # 1% per day
        company.save()

    def test_no_interest_before_due(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('100'),
        )
        self.assertEqual(services.compute_interest(rec, on_date=date(2026, 6, 20)), Decimal('0.00'))

    def test_interest_accrues_per_day(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('100'),
        )
        # 10 days late, 1%/day on balance 100 -> 10.00
        interest = services.compute_interest(rec, on_date=date(2026, 6, 30))
        self.assertEqual(interest, Decimal('10.00'))
        self.assertEqual(services.total_with_interest(rec, on_date=date(2026, 6, 30)), Decimal('110.00'))

    def test_no_interest_when_paid(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20),
            amount=Decimal('100'), paid_amount=Decimal('100'),
        )
        self.assertEqual(services.compute_interest(rec, on_date=date(2027, 1, 1)), Decimal('0.00'))

    def test_generate_for_rental_splits_total(self):
        recs = services.generate_for_rental(self.rental, installments=3, first_due_date=date(2026, 7, 1))
        self.assertEqual(len(recs), 3)
        self.assertEqual(sum(r.amount for r in recs), Decimal('300.00'))
        self.assertEqual([r.due_date for r in recs],
                         [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)])
