from datetime import date
from decimal import Decimal

from django.test import TestCase

from customers.models import Customer
from movements.models import Pickup, Return
from movements.services import compute_days_late, compute_penalty
from rentals.models import Rental


class PenaltyServiceTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            penalty_value=Decimal('20'),
        )

    def test_days_late_on_time(self):
        self.assertEqual(compute_days_late(date(2026, 6, 15), date(2026, 6, 15)), 0)

    def test_days_late_never_negative(self):
        self.assertEqual(compute_days_late(date(2026, 6, 15), date(2026, 6, 12)), 0)

    def test_days_late_counts_days(self):
        self.assertEqual(compute_days_late(date(2026, 6, 15), date(2026, 6, 18)), 3)

    def test_penalty_is_days_times_rate(self):
        self.assertEqual(compute_penalty(self.rental, 3), Decimal('60'))


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
