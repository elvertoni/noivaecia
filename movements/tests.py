from datetime import date
from decimal import Decimal

from django.db.models.signals import post_save
from django.test import TestCase

from customers.models import Customer
from movements.models import Pickup, Return
from movements.services import compute_days_late, compute_penalty
from company.models import Company
from rentals.models import Rental


class PenaltyServiceTests(TestCase):
    """Late return costs a daily slice of the rental, capped in days.

    Confirmed with the shop on 2026-08-02: 10% of the rental total per day, for
    at most 7 days, after which clause 6 charges the garment as not returned.
    """

    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.company = Company.objects.create(
            name='Noivas & Cia',
            late_return_daily_rate=Decimal('10'),
            late_return_max_days=7,
        )
        self.rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            total_value=Decimal('870'),
            # The replacement price: deliberately larger than the rental, and
            # deliberately not what the late fee is built from.
            penalty_value=Decimal('1250'),
        )

    def test_days_late_on_time(self):
        self.assertEqual(compute_days_late(date(2026, 6, 15), date(2026, 6, 15)), 0)

    def test_days_late_never_negative(self):
        self.assertEqual(compute_days_late(date(2026, 6, 15), date(2026, 6, 12)), 0)

    def test_days_late_counts_days(self):
        self.assertEqual(compute_days_late(date(2026, 6, 15), date(2026, 6, 18)), 3)

    def test_penalty_is_a_daily_share_of_the_rental_total(self):
        self.assertEqual(compute_penalty(self.rental, 1), Decimal('87.00'))
        self.assertEqual(compute_penalty(self.rental, 3), Decimal('261.00'))

    def test_penalty_stops_growing_after_the_day_cap(self):
        seven_days = compute_penalty(self.rental, 7)

        self.assertEqual(seven_days, Decimal('609.00'))
        self.assertEqual(compute_penalty(self.rental, 8), seven_days)
        self.assertEqual(compute_penalty(self.rental, 30), seven_days)

    def test_penalty_never_reaches_the_replacement_price(self):
        # The whole point of the cap: a late return must not cost more than
        # replacing the pieces, which is what clause 6 is for.
        self.assertLess(compute_penalty(self.rental, 30), self.rental.penalty_value)

    def test_penalty_ignores_the_replacement_price(self):
        self.rental.penalty_value = Decimal('0')

        self.assertEqual(compute_penalty(self.rental, 1), Decimal('87.00'))

    def test_penalty_uses_the_discounted_total(self):
        self.rental.cash_discount = True
        self.rental.cash_discount_amount = Decimal('70')

        # 870 - 70 = 800; 10% of that is what the customer actually owes per day.
        self.assertEqual(compute_penalty(self.rental, 1), Decimal('80.00'))

    def test_penalty_is_zero_when_not_late(self):
        self.assertEqual(compute_penalty(self.rental, 0), Decimal('0'))


class MovementSignalTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
        )

    def test_pickup_marks_rental_picked_up(self):
        Pickup.objects.create(rental=self.rental, pickup_date=date(2026, 6, 10))
        self.rental.refresh_from_db()
        self.assertEqual(self.rental.status, Rental.Status.PICKED_UP)

    def test_return_marks_rental_returned(self):
        Return.objects.create(rental=self.rental, return_date=date(2026, 6, 16))
        self.rental.refresh_from_db()
        self.assertEqual(self.rental.status, Rental.Status.RETURNED)

    def test_raw_pickup_does_not_change_rental_status(self):
        initial_status = self.rental.status
        post_save.send(
            sender=Pickup,
            instance=Pickup(rental=self.rental, pickup_date=date(2026, 6, 10)),
            created=True,
            raw=True,
            using='default',
            update_fields=None,
        )

        self.rental.refresh_from_db()
        self.assertEqual(self.rental.status, initial_status)

    def test_raw_return_does_not_change_rental_status(self):
        initial_status = self.rental.status
        post_save.send(
            sender=Return,
            instance=Return(rental=self.rental, return_date=date(2026, 6, 16)),
            created=True,
            raw=True,
            using='default',
            update_fields=None,
        )

        self.rental.refresh_from_db()
        self.assertEqual(self.rental.status, initial_status)
