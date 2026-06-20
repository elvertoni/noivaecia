"""Tests for Sprint R11 — reports, print, CSV export."""
import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from billing.models import Receivable
from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer
from movements.models import Pickup, Return
from rentals.models import Rental, RentalItem
from reports.services import report_a_retirar

User = get_user_model()
TODAY = date.today()


def _streaming_csv_rows(response):
    content = []
    for chunk in response.streaming_content:
        if isinstance(chunk, bytes):
            content.append(chunk.decode('utf-8-sig'))
        else:
            content.append(chunk.lstrip('\ufeff'))
    return list(csv.reader(io.StringIO(''.join(content)), delimiter=';'))


def _make_company():
    Company.objects.filter(pk=1).delete()
    return Company.objects.create(name='Noivas Teste', last_rental_number=1, daily_interest_rate=Decimal('0'))


def _make_user(modules=('reports',)):
    user = User.objects.create_user(email='r11@test.com', password='pass')
    for m in modules:
        ModulePermission.objects.create(user=user, module_key=m, allowed=True)
    ActionPermission.objects.create(user=user, action_key='reports.export', allowed=True)
    return user


def _make_rental(number=100, status='pending', pickup_date=None, return_date=None):
    customer = Customer.objects.create(name=f'Cliente {number}', city='Recife')
    pickup_date = pickup_date or TODAY
    return_date = return_date or TODAY + timedelta(days=7)
    return Rental.objects.create(
        number=number, customer=customer,
        pickup_date=pickup_date, return_date=return_date,
        total_value=Decimal('200'),
        status=status,
    )


def _make_receivable(rental, balance=Decimal('100'), due_date=None):
    """Create a Receivable with the given open balance.

    Receivable.save() computes balance = amount - paid_amount, so we derive
    paid_amount from amount and the desired balance instead of setting balance
    directly (which would be overwritten).
    """
    due_date = due_date or TODAY + timedelta(days=30)
    amount = Decimal('200')
    paid_amount = amount - balance
    return Receivable.objects.create(
        rental=rental, due_date=due_date,
        amount=amount, paid_amount=paid_amount,
    )


class ReportsIndexViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)

    def test_200_renders(self):
        r = self.client.get(reverse('reports:index'))
        self.assertEqual(r.status_code, 200)


class ARetirarReportViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('reports:a_retirar')
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

    def test_csv_export(self):
        r = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])
        self.assertIn('attachment', r.get('Content-Disposition', ''))

    def test_csv_export_is_streaming(self):
        r = self.client.get(self.url, {'format': 'csv'})

        self.assertTrue(r.streaming)

    def test_csv_export_keeps_report_limit(self):
        _make_rental(202, status='pending')
        _make_rental(203, status='pending')

        with mock.patch('reports.views.ARetirarReportView.report_limit', 1):
            r = self.client.get(self.url, {'format': 'csv'})
            rows = _streaming_csv_rows(r)

        self.assertEqual(len(rows), 2)  # header + one data row


class ReportQueryPerformanceTests(TestCase):
    def setUp(self):
        _make_company()
        self.category = Category.objects.create(prefix='VES', name='Vestidos')
        self.product = Product.objects.create(
            category=self.category,
            code=1,
            description='Vestido',
            value=Decimal('100'),
        )

    def _rental_with_item(self, number):
        rental = _make_rental(number, status='pending')
        RentalItem.objects.create(
            rental=rental,
            product=self.product,
            value=Decimal('100'),
            # proof_photo is a FileField — give it a named file, not raw bytes.
            proof_photo=ContentFile(b'large-binary', name='proof.jpg'),
            proof_photo_size=12,
        )
        return rental

    def test_rental_report_does_not_distinct_without_item_filters(self):
        self._rental_with_item(210)

        sql = str(report_a_retirar(max_results=None).query).upper()

        self.assertNotIn('DISTINCT', sql)

    def test_rental_report_distinct_only_when_item_filters_join(self):
        self._rental_with_item(211)

        sql = str(report_a_retirar(prefix='VES', max_results=None).query).upper()

        self.assertIn('DISTINCT', sql)

    def test_rental_report_defers_item_proof_photo_blob(self):
        rental = self._rental_with_item(212)

        result = report_a_retirar(code='1', max_results=None).get(pk=rental.pk)
        item = result.items.all()[0]

        self.assertIn('proof_photo', item.get_deferred_fields())

    def test_rental_report_applies_default_display_limit(self):
        self._rental_with_item(213)
        self._rental_with_item(214)
        self._rental_with_item(215)

        rentals = list(report_a_retirar(max_results=2))

        self.assertEqual(len(rentals), 2)


class RetiradosReportViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('reports:retirados')
        self.picked = _make_rental(300, status='picked_up')
        self.pending = _make_rental(301, status='pending')

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_shows_only_picked_up(self):
        r = self.client.get(self.url)
        pks = {rental.pk for rental in r.context['rentals']}
        self.assertIn(self.picked.pk, pks)
        self.assertNotIn(self.pending.pk, pks)

    def test_csv_export(self):
        r = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])


class DevolvidosReportViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('reports:devolvidos')
        self.returned = _make_rental(400, status='returned')
        self.pending = _make_rental(401, status='pending')

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_shows_only_returned(self):
        r = self.client.get(self.url)
        pks = {rental.pk for rental in r.context['rentals']}
        self.assertIn(self.returned.pk, pks)
        self.assertNotIn(self.pending.pk, pks)

    def test_csv_export(self):
        r = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])


class AtrasadosReportViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('reports:atrasados')
        self.overdue = _make_rental(500, status='picked_up', return_date=TODAY - timedelta(days=4))
        self.on_time = _make_rental(501, status='picked_up', return_date=TODAY + timedelta(days=3))

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_shows_only_overdue(self):
        r = self.client.get(self.url)
        pks = {e['rental'].pk for e in r.context['overdue']}
        self.assertIn(self.overdue.pk, pks)
        self.assertNotIn(self.on_time.pk, pks)

    def test_days_late_correct(self):
        r = self.client.get(self.url)
        entry = next(e for e in r.context['overdue'] if e['rental'].pk == self.overdue.pk)
        self.assertEqual(entry['days_late'], 4)

    def test_csv_export(self):
        r = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])


class LocacoesReportViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('reports:locacoes')
        self.rental1 = _make_rental(600, status='pending')
        self.rental2 = _make_rental(601, status='returned')
        self.cancelled = _make_rental(602, status='cancelled')

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_excludes_cancelled(self):
        r = self.client.get(self.url)
        pks = {rental.pk for rental in r.context['rentals']}
        self.assertNotIn(self.cancelled.pk, pks)

    def test_filter_by_status(self):
        r = self.client.get(self.url, {'status': 'returned'})
        pks = {rental.pk for rental in r.context['rentals']}
        self.assertIn(self.rental2.pk, pks)
        self.assertNotIn(self.rental1.pk, pks)

    def test_csv_export(self):
        r = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])


class ContasVencimentoReportViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('reports:contas_vencimento')
        self.rental = _make_rental(700, status='pending')
        # Open receivable: balance = 200 - 50 = 150
        self.open_rec = _make_receivable(self.rental, balance=Decimal('150'))
        # Paid receivable: balance = 50 - 50 = 0
        self.paid_rec = Receivable.objects.create(
            rental=self.rental, due_date=TODAY + timedelta(days=10),
            amount=Decimal('50'), paid_amount=Decimal('50'),
        )

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_shows_only_open(self):
        r = self.client.get(self.url)
        pks = {rec.pk for rec in r.context['receivables']}
        self.assertIn(self.open_rec.pk, pks)
        self.assertNotIn(self.paid_rec.pk, pks)

    def test_totals_in_context(self):
        r = self.client.get(self.url)
        self.assertIn('totals', r.context)
        self.assertIsNotNone(r.context['totals']['t_balance'])

    def test_overdue_filter(self):
        # past_rec has a due_date in the past — should appear with overdue=1
        past_rec = _make_receivable(self.rental, balance=Decimal('75'), due_date=TODAY - timedelta(days=5))
        r = self.client.get(self.url, {'overdue': '1'})
        pks = {rec.pk for rec in r.context['receivables']}
        self.assertIn(past_rec.pk, pks)
        # open_rec has a future due date — must NOT appear under overdue-only filter
        self.assertNotIn(self.open_rec.pk, pks)

    def test_csv_export(self):
        r = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])


class ContasClienteReportViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('reports:contas_cliente')
        self.rental = _make_rental(800, status='pending')
        _make_receivable(self.rental, balance=Decimal('200'))

    def test_200_renders(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_groups_in_context(self):
        r = self.client.get(self.url)
        self.assertIn('groups', r.context)
        groups = r.context['groups']
        # At least one group
        self.assertGreater(len(groups), 0)

    def test_group_has_subtotals(self):
        r = self.client.get(self.url)
        group = r.context['groups'][0]
        self.assertIn('total_balance', group)
        self.assertIn('total_amount', group)

    def test_filter_by_customer(self):
        r = self.client.get(self.url, {'customer': 'Cliente 800'})
        groups = r.context['groups']
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['customer'].name, 'Cliente 800')

    def test_status_open_filter(self):
        # Paid receivable for another rental should not appear with status=open
        rental2 = _make_rental(801, status='returned')
        Receivable.objects.create(
            rental=rental2, due_date=TODAY,
            amount=Decimal('100'), paid_amount=Decimal('100'),
        )
        r = self.client.get(self.url, {'status': 'open'})
        # rental2's customer should not appear since balance=0
        names = [g['customer'].name for g in r.context['groups']]
        self.assertNotIn('Cliente 801', names)

    def test_csv_export(self):
        r = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])
