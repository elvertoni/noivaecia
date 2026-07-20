"""Tests for the daily WhatsApp report builder (Fase 3). No test touches the
network — this module only exercises ORM fixtures and string assembly."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.models import Receivable
from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer
from notifications.services import build_daily_report
from rentals.models import Rental, RentalItem

TODAY = date(2026, 7, 20)


def _make_company():
    Company.objects.filter(pk=1).delete()
    return Company.objects.create(name='Noivas Cia', last_rental_number=1)


def _make_customer(name):
    return Customer.objects.create(name=name, city='Bandeirantes')


def _make_product(code, description='Vestido de festa'):
    category = Category.objects.get_or_create(prefix='VF', defaults={'name': 'Vestido de festa'})[0]
    return Product.objects.create(category=category, code=code, description=description, value=Decimal('300'))


def _make_rental(number, customer, status, pickup_date, return_date, with_item=True):
    rental = Rental.objects.create(
        number=number, customer=customer, status=status,
        pickup_date=pickup_date, return_date=return_date,
        total_value=Decimal('300'),
    )
    if with_item:
        product = _make_product(100 + number)
        RentalItem.objects.create(rental=rental, product=product, value=Decimal('300'))
    return rental


def _make_receivable(rental, due_date, amount=Decimal('300'), paid=Decimal('0')):
    return Receivable.objects.create(
        rental=rental, due_date=due_date, amount=amount, paid_amount=paid,
    )


class EmptyDayTests(TestCase):
    def setUp(self):
        _make_company()

    def test_nothing_scheduled_returns_short_message(self):
        text = build_daily_report(TODAY)
        self.assertIn('Sem entregas, retiradas ou vencimentos hoje', text)
        self.assertIn('resumo de seg, 20/07', text)
        self.assertNotIn('📦', text)
        self.assertNotIn('👗', text)
        self.assertNotIn('💰', text)


class DeliveriesBlockTests(TestCase):
    """Block (a) — Rental picked_up with return_date == today (+ overdue)."""

    def setUp(self):
        _make_company()
        self.customer = _make_customer('Maria Silva')

    def test_delivery_due_today_is_listed(self):
        _make_rental(
            1, self.customer, Rental.Status.PICKED_UP,
            pickup_date=TODAY - timedelta(days=5), return_date=TODAY,
        )
        text = build_daily_report(TODAY)
        self.assertIn('📦 *Entregas a fazer hoje: 1*', text)
        self.assertIn('#1 Maria Silva', text)
        self.assertNotIn('⚠️', text)

    def test_overdue_delivery_is_flagged_and_not_counted_as_today(self):
        _make_rental(
            2, self.customer, Rental.Status.PICKED_UP,
            pickup_date=TODAY - timedelta(days=10), return_date=TODAY - timedelta(days=3),
        )
        text = build_daily_report(TODAY)
        self.assertIn('📦 *Entregas a fazer hoje: 0*', text)
        self.assertIn('⚠️ 1 devolução atrasada', text)

    def test_multiple_overdue_deliveries_use_plural(self):
        _make_rental(3, self.customer, Rental.Status.PICKED_UP,
                     pickup_date=TODAY - timedelta(days=10), return_date=TODAY - timedelta(days=3))
        _make_rental(4, self.customer, Rental.Status.PICKED_UP,
                     pickup_date=TODAY - timedelta(days=10), return_date=TODAY - timedelta(days=1))
        text = build_daily_report(TODAY)
        self.assertIn('⚠️ 2 devoluções atrasadas', text)

    def test_pending_and_returned_rentals_are_not_deliveries(self):
        _make_rental(5, self.customer, Rental.Status.PENDING,
                     pickup_date=TODAY, return_date=TODAY + timedelta(days=5))
        _make_rental(6, self.customer, Rental.Status.RETURNED,
                     pickup_date=TODAY - timedelta(days=10), return_date=TODAY)
        text = build_daily_report(TODAY)
        self.assertIn('📦 *Entregas a fazer hoje: 0*', text)


class PickupsBlockTests(TestCase):
    """Block (b) — Rental pending with pickup_date == today (+ overdue)."""

    def setUp(self):
        _make_company()
        self.customer = _make_customer('Ana Costa')

    def test_pickup_due_today_is_listed(self):
        _make_rental(10, self.customer, Rental.Status.PENDING,
                     pickup_date=TODAY, return_date=TODAY + timedelta(days=7))
        text = build_daily_report(TODAY)
        self.assertIn('👗 *Retiradas de hoje: 1*', text)
        self.assertIn('#10 Ana Costa', text)

    def test_overdue_pickup_is_flagged(self):
        _make_rental(11, self.customer, Rental.Status.PENDING,
                     pickup_date=TODAY - timedelta(days=2), return_date=TODAY + timedelta(days=5))
        text = build_daily_report(TODAY)
        self.assertIn('👗 *Retiradas de hoje: 0*', text)
        self.assertIn('⚠️ 1 retirada atrasada', text)


class ReceivablesBlockTests(TestCase):
    """Block (c) — Receivable open, due_date == today (+ overdue total)."""

    def setUp(self):
        _make_company()
        self.customer = _make_customer('Carla Souza')
        self.rental = _make_rental(20, self.customer, Rental.Status.RETURNED,
                                    pickup_date=TODAY - timedelta(days=20),
                                    return_date=TODAY - timedelta(days=10))

    def test_receivable_due_today_is_listed_with_brl_amount(self):
        _make_receivable(self.rental, due_date=TODAY, amount=Decimal('300'))
        text = build_daily_report(TODAY)
        self.assertIn('💰 *A receber hoje: R$ 300,00 (1 título)*', text)
        self.assertIn('#20 Carla Souza — R$ 300,00', text)

    def test_overdue_receivables_are_summed_separately(self):
        rental2 = _make_rental(21, self.customer, Rental.Status.RETURNED,
                                pickup_date=TODAY - timedelta(days=30),
                                return_date=TODAY - timedelta(days=20))
        _make_receivable(self.rental, due_date=TODAY - timedelta(days=5), amount=Decimal('100'))
        _make_receivable(rental2, due_date=TODAY - timedelta(days=1), amount=Decimal('180'))
        text = build_daily_report(TODAY)
        self.assertIn('💰 *A receber hoje: R$ 0,00 (0 títulos)*', text)
        self.assertIn('Vencidos em aberto: R$ 280,00 (2 títulos)', text)

    def test_paid_receivable_is_excluded(self):
        _make_receivable(self.rental, due_date=TODAY, amount=Decimal('300'), paid=Decimal('300'))
        text = build_daily_report(TODAY)
        self.assertIn('Sem entregas, retiradas ou vencimentos hoje', text)

    def test_written_off_receivable_is_excluded(self):
        rec = _make_receivable(self.rental, due_date=TODAY - timedelta(days=5), amount=Decimal('300'))
        rec.written_off_at = timezone.now()
        rec.save()
        text = build_daily_report(TODAY)
        self.assertIn('Sem entregas, retiradas ou vencimentos hoje', text)

    def test_future_and_past_far_receivable_do_not_leak_into_wrong_bucket(self):
        _make_receivable(self.rental, due_date=TODAY + timedelta(days=3), amount=Decimal('50'))
        text = build_daily_report(TODAY)
        self.assertIn('Sem entregas, retiradas ou vencimentos hoje', text)


class TruncationTests(TestCase):
    """Each block lists at most 15 items, with a '+N outros' trailer."""

    def setUp(self):
        _make_company()

    def test_more_than_fifteen_deliveries_are_truncated(self):
        customer = _make_customer('Cliente Volume')
        for i in range(20):
            _make_rental(
                100 + i, customer, Rental.Status.PICKED_UP,
                pickup_date=TODAY - timedelta(days=5), return_date=TODAY,
            )
        text = build_daily_report(TODAY)
        self.assertIn('📦 *Entregas a fazer hoje: 20*', text)
        self.assertIn('+5 outros', text)
        # Only 15 bullet lines for the deliveries block.
        deliveries_section = text.split('📦')[1].split('👗')[0]
        self.assertEqual(deliveries_section.count('•'), 15)

    def test_more_than_fifteen_receivables_are_truncated(self):
        customer = _make_customer('Cliente Titulos')
        rental = _make_rental(200, customer, Rental.Status.RETURNED,
                               pickup_date=TODAY - timedelta(days=20),
                               return_date=TODAY - timedelta(days=10), with_item=False)
        for i in range(17):
            Receivable.objects.create(
                rental=rental, due_date=TODAY, amount=Decimal('10'),
            )
        text = build_daily_report(TODAY)
        self.assertIn('(17 títulos)*', text)
        self.assertIn('+2 outros', text)


class CombinedBlocksTests(TestCase):
    """All three blocks together render in one message."""

    def setUp(self):
        _make_company()

    def test_all_blocks_present_together(self):
        customer = _make_customer('Combo Cliente')
        delivery_rental = _make_rental(
            300, customer, Rental.Status.PICKED_UP,
            pickup_date=TODAY - timedelta(days=5), return_date=TODAY,
        )
        _make_rental(
            301, customer, Rental.Status.PENDING,
            pickup_date=TODAY, return_date=TODAY + timedelta(days=7),
        )
        _make_receivable(delivery_rental, due_date=TODAY, amount=Decimal('300'))

        text = build_daily_report(TODAY)
        self.assertIn('📦 *Entregas a fazer hoje: 1*', text)
        self.assertIn('👗 *Retiradas de hoje: 1*', text)
        self.assertIn('💰 *A receber hoje: R$ 300,00 (1 título)*', text)
        self.assertIn('resumo de seg, 20/07', text)
