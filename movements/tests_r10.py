"""Tests for Sprint R10 — pickup list, return list, overdue, pickup/return status, penalty receivable, payment on return."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from billing.models import CashAccount, Receivable
from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer
from movements.models import Pickup, Return
from rentals.models import Rental, RentalItem

User = get_user_model()
TODAY = date.today()


def _make_user(modules=('movements',)):
    user = User.objects.create_user(email='r10@test.com', password='pass')
    for m in modules:
        ModulePermission.objects.create(user=user, module_key=m, allowed=True)
    return user


def _make_company():
    Company.objects.filter(pk=1).delete()
    return Company.objects.create(name='T', last_rental_number=1, daily_interest_rate=Decimal('0'))


def _make_rental(number=200, status='pending', pickup_date=None, return_date=None):
    customer = Customer.objects.create(name=f'Cliente {number}', city='Recife')
    pickup_date = pickup_date or TODAY
    return_date = return_date or TODAY + timedelta(days=7)
    return Rental.objects.create(
        number=number, customer=customer,
        pickup_date=pickup_date, return_date=return_date,
        total_value=Decimal('300'),
        status=status,
        penalty_value=Decimal('50'),
    )


def _make_cash_account():
    return CashAccount.objects.get_or_create(name='Caixa Principal', defaults={'active': True})[0]


# ── R10.01 PickupListView ─────────────────────────────────────────────────────

class PickupListViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('movements:pickup_list')
        self.pending = _make_rental(200, status='pending')
        self.picked = _make_rental(201, status='picked_up')

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_shows_only_pending(self):
        r = self.client.get(self.url)
        pks = {rental.pk for rental in r.context['rentals']}
        self.assertIn(self.pending.pk, pks)
        self.assertNotIn(self.picked.pk, pks)

    def test_filter_by_customer(self):
        r = self.client.get(self.url, {'customer': f'Cliente {self.pending.number}'})
        pks = {rental.pk for rental in r.context['rentals']}
        self.assertIn(self.pending.pk, pks)


# ── R10.02 Pickup status update ───────────────────────────────────────────────

class PickupStatusUpdateTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.rental = _make_rental(300, status='pending')

    def test_pickup_updates_rental_status(self):
        url = reverse('movements:pickup', kwargs={'rental_pk': self.rental.pk})
        self.client.post(url, {'pickup_date': TODAY.isoformat()})
        self.rental.refresh_from_db()
        self.assertEqual(self.rental.status, Rental.Status.PICKED_UP)

    def test_pickup_creates_pickup_record(self):
        url = reverse('movements:pickup', kwargs={'rental_pk': self.rental.pk})
        self.client.post(url, {'pickup_date': TODAY.isoformat()})
        self.assertTrue(Pickup.objects.filter(rental=self.rental).exists())


# ── R10.03 ReturnListView ─────────────────────────────────────────────────────

class ReturnListViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('movements:return_list')
        self.picked = _make_rental(400, status='picked_up')
        self.pending = _make_rental(401, status='pending')

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_shows_only_picked_up(self):
        r = self.client.get(self.url)
        pks = {rental.pk for rental in r.context['rentals']}
        self.assertIn(self.picked.pk, pks)
        self.assertNotIn(self.pending.pk, pks)


class ReturnListUrgentPickupTests(TestCase):
    """A piece still out is flagged urgent when already booked for pickup soon."""

    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('movements:return_list')
        category = Category.objects.create(prefix='VN', name='Vestidos')
        self.product = Product.objects.create(category=category, code=1, description='Vestido A', value=100)

    def test_flags_urgent_when_product_booked_within_10_days(self):
        out_rental = _make_rental(410, status='picked_up')
        RentalItem.objects.create(rental=out_rental, product=self.product, value=Decimal('100'))
        booked = _make_rental(411, status='pending', pickup_date=TODAY + timedelta(days=5))
        RentalItem.objects.create(rental=booked, product=self.product, value=Decimal('100'))

        r = self.client.get(self.url)
        rental = next(x for x in r.context['rentals'] if x.pk == out_rental.pk)
        self.assertEqual(rental.urgent_pickup.pk, booked.pk)

    def test_no_flag_when_next_pickup_is_beyond_window(self):
        out_rental = _make_rental(412, status='picked_up')
        RentalItem.objects.create(rental=out_rental, product=self.product, value=Decimal('100'))
        far_booked = _make_rental(413, status='pending', pickup_date=TODAY + timedelta(days=20))
        RentalItem.objects.create(rental=far_booked, product=self.product, value=Decimal('100'))

        r = self.client.get(self.url)
        rental = next(x for x in r.context['rentals'] if x.pk == out_rental.pk)
        self.assertIsNone(rental.urgent_pickup)

    def test_no_flag_when_product_not_booked(self):
        out_rental = _make_rental(414, status='picked_up')
        RentalItem.objects.create(rental=out_rental, product=self.product, value=Decimal('100'))

        r = self.client.get(self.url)
        rental = next(x for x in r.context['rentals'] if x.pk == out_rental.pk)
        self.assertIsNone(rental.urgent_pickup)


# ── R10.04 OverdueListView ────────────────────────────────────────────────────

class OverdueListViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('movements:overdue_list')
        self.overdue = _make_rental(500, status='picked_up', return_date=TODAY - timedelta(days=3))
        self.on_time = _make_rental(501, status='picked_up', return_date=TODAY + timedelta(days=5))

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_shows_only_overdue(self):
        r = self.client.get(self.url)
        overdue_rental_pks = {e['rental'].pk for e in r.context['overdue']}
        self.assertIn(self.overdue.pk, overdue_rental_pks)
        self.assertNotIn(self.on_time.pk, overdue_rental_pks)

    def test_days_late_correct(self):
        r = self.client.get(self.url)
        entry = next(e for e in r.context['overdue'] if e['rental'].pk == self.overdue.pk)
        self.assertEqual(entry['days_late'], 3)


# ── R10.05 Return with financial balance ──────────────────────────────────────

class ReturnFinancialContextTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.rental = _make_rental(600, status='picked_up')
        Pickup.objects.create(rental=self.rental, pickup_date=TODAY - timedelta(days=2))
        self.rec = Receivable.objects.create(
            rental=self.rental, due_date=TODAY + timedelta(days=5),
            amount=Decimal('300'), balance=Decimal('300'),
        )

    def test_return_form_shows_open_balance(self):
        url = reverse('movements:return', kwargs={'rental_pk': self.rental.pk})
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn('total_open_balance', r.context)
        self.assertEqual(r.context['total_open_balance'], Decimal('300'))


# ── R10.05 Payment on return ──────────────────────────────────────────────────

class ReturnWithPaymentTests(TestCase):
    def setUp(self):
        _make_company()
        _make_cash_account()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.rental = _make_rental(700, status='picked_up', return_date=TODAY)
        Pickup.objects.create(rental=self.rental, pickup_date=TODAY - timedelta(days=1))
        self.rec = Receivable.objects.create(
            rental=self.rental, due_date=TODAY,
            amount=Decimal('300'), balance=Decimal('300'),
        )

    def test_return_with_payment_reduces_balance(self):
        url = reverse('movements:return', kwargs={'rental_pk': self.rental.pk})
        self.client.post(url, {
            'return_date': TODAY.isoformat(),
            'payment_amount': '150.00',
            'payment_method': 'cash',
            'payment_date': TODAY.isoformat(),
        })
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.balance, Decimal('150'))


# ── R10.05 Return status update ───────────────────────────────────────────────

class ReturnStatusUpdateTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.rental = _make_rental(800, status='picked_up', return_date=TODAY)
        Pickup.objects.create(rental=self.rental, pickup_date=TODAY - timedelta(days=1))

    def test_return_updates_rental_status(self):
        url = reverse('movements:return', kwargs={'rental_pk': self.rental.pk})
        self.client.post(url, {'return_date': TODAY.isoformat()})
        self.rental.refresh_from_db()
        self.assertEqual(self.rental.status, Rental.Status.RETURNED)

    def test_return_creates_return_record(self):
        url = reverse('movements:return', kwargs={'rental_pk': self.rental.pk})
        self.client.post(url, {'return_date': TODAY.isoformat()})
        self.assertTrue(Return.objects.filter(rental=self.rental).exists())


# ── R10.06 Penalty receivable ─────────────────────────────────────────────────

class ReturnPenaltyReceivableTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        # return_date in the past so return is late
        past_return = TODAY - timedelta(days=5)
        self.rental = _make_rental(900, status='picked_up', return_date=past_return)
        self.rental.penalty_value = Decimal('20')
        self.rental.save()
        Pickup.objects.create(rental=self.rental, pickup_date=past_return - timedelta(days=3))

    def test_late_return_creates_penalty_receivable(self):
        url = reverse('movements:return', kwargs={'rental_pk': self.rental.pk})
        before_count = Receivable.objects.filter(rental=self.rental).count()
        self.client.post(url, {'return_date': TODAY.isoformat()})
        after_count = Receivable.objects.filter(rental=self.rental).count()
        self.assertGreater(after_count, before_count)

    def test_on_time_return_no_penalty_receivable(self):
        future_return = TODAY + timedelta(days=5)
        self.rental.return_date = future_return
        self.rental.save()
        url = reverse('movements:return', kwargs={'rental_pk': self.rental.pk})
        before_count = Receivable.objects.filter(rental=self.rental).count()
        self.client.post(url, {'return_date': TODAY.isoformat()})
        after_count = Receivable.objects.filter(rental=self.rental).count()
        self.assertEqual(after_count, before_count)


# ── R10.07 SettleReturnsView ──────────────────────────────────────────────────

class SettleReturnsViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user(modules=('maintenance',))
        self.client.force_login(self.user)
        self.url = reverse('maintenance:settle_returns')

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_finds_returned_without_record(self):
        rental = _make_rental(1001, status='returned')
        r = self.client.get(self.url)
        pks = [rent.pk for rent in r.context['returned_no_record']]
        self.assertIn(rental.pk, pks)

    def test_finds_picked_no_pickup(self):
        rental = _make_rental(1002, status='picked_up')
        r = self.client.get(self.url)
        pks = [rent.pk for rent in r.context['picked_no_pickup']]
        self.assertIn(rental.pk, pks)

    def test_clean_db_has_no_issues(self):
        r = self.client.get(self.url)
        self.assertEqual(r.context['total_issues'], 0)
