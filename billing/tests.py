from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from billing import services
from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from company.models import Company
from customers.models import Customer
from rentals.models import Rental
from django.urls import reverse


def make_rental(number=1):
    customer = Customer.objects.create(name='Maria')
    return Rental.objects.create(
        number=number, customer=customer,
        pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
    )


class ReceivableModelTests(TestCase):
    def setUp(self):
        self.rental = make_rental()

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

    def test_legacy_fields_store_correctly(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('100'),
            legacy_id=9999, legacy_source='pagar', legacy_notes='pago=1',
        )
        rec.refresh_from_db()
        self.assertEqual(rec.legacy_id, 9999)
        self.assertEqual(rec.legacy_source, 'pagar')

    def test_recalculate_from_payments(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('200'),
        )
        account = CashAccount.objects.create(name='Caixa')
        Payment.objects.create(
            receivable=rec, payment_date=date(2026, 6, 21), amount=Decimal('80'),
        )
        Payment.objects.create(
            receivable=rec, payment_date=date(2026, 6, 22), amount=Decimal('70'),
        )
        rec.recalculate_from_payments()
        self.assertEqual(rec.paid_amount, Decimal('150'))
        self.assertEqual(rec.balance, Decimal('50'))

    def test_report_indexes_declared(self):
        index_names = {index.name for index in Receivable._meta.indexes}

        self.assertIn('rcv_overdue_idx', index_names)
        self.assertIn('rcv_balance_due_idx', index_names)
        self.assertIn('rcv_rental_due_idx', index_names)

    def test_recalculate_from_payments_updates_last_payment_date(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('200'),
        )
        Payment.objects.create(
            receivable=rec, payment_date=date(2026, 6, 21), amount=Decimal('80'),
        )
        Payment.objects.create(
            receivable=rec, payment_date=date(2026, 6, 22), amount=Decimal('70'),
        )
        rec.recalculate_from_payments()

        self.assertEqual(rec.last_payment_date, date(2026, 6, 22))


class InterestServiceTests(TestCase):
    def setUp(self):
        self.rental = make_rental()
        self.rental.total_value = Decimal('300')
        self.rental.save()
        company = Company.load()
        company.daily_interest_rate = Decimal('1.00')
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
        interest = services.compute_interest(rec, on_date=date(2026, 6, 30))
        self.assertEqual(interest, Decimal('10.00'))
        self.assertEqual(services.total_with_interest(rec, on_date=date(2026, 6, 30)), Decimal('110.00'))

    def test_interest_uses_provided_company_without_loading_config(self):
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('100'),
        )
        company = Company.load()

        with mock.patch('billing.services.Company.load') as load:
            interest = services.compute_interest(
                rec, on_date=date(2026, 6, 30), company=company
            )

        self.assertEqual(interest, Decimal('10.00'))
        load.assert_not_called()

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


class CashAccountTests(TestCase):
    def test_create_cash_account(self):
        acc = CashAccount.objects.create(name='Caixa Principal', legacy_code='1')
        self.assertTrue(acc.active)
        self.assertEqual(str(acc), 'Caixa Principal')

    def test_inactive_account(self):
        acc = CashAccount.objects.create(name='Conta Antiga', active=False)
        self.assertFalse(acc.active)


class PaymentModelTests(TestCase):
    def setUp(self):
        self.rental = make_rental()
        self.rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 7, 1), amount=Decimal('300'),
        )

    def test_create_payment(self):
        p = Payment.objects.create(
            receivable=self.rec,
            payment_date=date(2026, 7, 5),
            amount=Decimal('150'),
            method=Payment.Method.PIX,
        )
        self.assertEqual(p.amount, Decimal('150'))
        self.assertFalse(p.is_reversal)

    def test_payment_with_legacy_movement_id(self):
        p = Payment.objects.create(
            receivable=self.rec,
            payment_date=date(2026, 7, 5),
            amount=Decimal('100'),
            legacy_movement_id=12345,
        )
        self.assertEqual(p.legacy_movement_id, 12345)

    def test_pagar_pago1_open_semantics(self):
        # pago=1 in legacy = open (balance > 0)
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 7, 1),
            amount=Decimal('500'), paid_amount=Decimal('0'),
            legacy_id=42, legacy_source='pagar', legacy_notes='pago=1',
        )
        self.assertFalse(rec.is_paid)
        self.assertEqual(rec.balance, Decimal('500'))

    def test_pagar_pago0_closed_semantics(self):
        # pago=0 in legacy = closed (paid in full)
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 7, 1),
            amount=Decimal('500'), paid_amount=Decimal('500'),
            legacy_id=43, legacy_source='pagar', legacy_notes='pago=0',
        )
        self.assertTrue(rec.is_paid)
        self.assertEqual(rec.balance, Decimal('0'))

    def test_pagar_pago1_partial_payment(self):
        # pago=1 with partial valor_pago = partially paid, still open
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 7, 1),
            amount=Decimal('500'), paid_amount=Decimal('200'),
            legacy_id=44, legacy_source='pagar', legacy_notes='pago=1,parcial',
        )
        self.assertFalse(rec.is_paid)
        self.assertEqual(rec.balance, Decimal('300'))

    def test_customer_date_index_declared(self):
        index_names = {index.name for index in Payment._meta.indexes}

        self.assertIn('pmt_customer_date_idx', index_names)
        self.assertIn('pmt_reversal_date_idx', index_names)


class FinancialMovementTests(TestCase):
    def setUp(self):
        self.rental = make_rental()
        self.account = CashAccount.objects.create(name='Caixa')
        self.rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 7, 1), amount=Decimal('200'),
        )

    def test_create_inflow_movement(self):
        mv = FinancialMovement.objects.create(
            date=date(2026, 7, 5),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('200'),
            source=FinancialMovement.Source.PAYMENT,
            description='Recebimento locação',
            receivable=self.rec,
        )
        self.assertEqual(mv.direction, 'inflow')
        self.assertEqual(str(mv), 'Entrada R$200 · 2026-07-05')

    def test_movement_can_link_payment(self):
        payment = Payment.objects.create(
            receivable=self.rec,
            payment_date=date(2026, 7, 5),
            amount=Decimal('200'),
        )
        mv = FinancialMovement.objects.create(
            date=date(2026, 7, 5),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('200'),
            source=FinancialMovement.Source.PAYMENT,
            receivable=self.rec,
            payment=payment,
        )
        self.assertEqual(mv.payment, payment)

    def test_create_manual_outflow(self):
        mv = FinancialMovement.objects.create(
            date=date(2026, 7, 6),
            account=self.account,
            direction=FinancialMovement.Direction.OUTFLOW,
            amount=Decimal('50'),
            source=FinancialMovement.Source.MANUAL,
            description='Despesa operacional',
        )
        self.assertEqual(mv.source, 'manual')

    def test_legacy_id_stored(self):
        mv = FinancialMovement.objects.create(
            date=date(2026, 7, 1),
            account=self.account,
            direction=FinancialMovement.Direction.INFLOW,
            amount=Decimal('100'),
            source=FinancialMovement.Source.IMPORT,
            legacy_id=9876,
        )
        self.assertEqual(mv.legacy_id, 9876)

    def test_date_direction_index_declared(self):
        index_names = {index.name for index in FinancialMovement._meta.indexes}

        self.assertIn('fmv_date_created_idx', index_names)
        self.assertIn('fmv_direction_date_idx', index_names)
        self.assertIn('fmv_source_direction_date_idx', index_names)


class GenerateReceivablesViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_superuser(username='admin', password='password')
        self.client.login(username='admin', password='password')
        self.rental = make_rental()

    def test_re_generate_deletes_old_unpaid_receivables(self):
        # Create an existing unpaid receivable (e.g. representing the default 1 installment)
        old_rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('300.00')
        )
        self.assertEqual(self.rental.receivables.count(), 1)

        # Call the generate view to split into 3 installments
        url = reverse('billing:generate', args=[self.rental.pk])
        response = self.client.post(url, {
            'installments': 3,
            'first_due_date': '2026-07-01'
        })
        self.assertEqual(response.status_code, 302)  # Redirect to billing:list

        # Validate that the old unpaid receivable was deleted and 3 new ones were created
        self.assertEqual(self.rental.receivables.count(), 3)
        self.assertFalse(Receivable.objects.filter(pk=old_rec.pk).exists())
        self.assertEqual(sum(r.amount for r in self.rental.receivables.all()), Decimal('300.00'))

    def test_re_generate_blocked_when_payments_exist(self):
        # Create an existing receivable
        rec = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('300.00')
        )
        # Register a payment against it
        Payment.objects.create(
            receivable=rec,
            payment_date=date(2026, 6, 20),
            amount=Decimal('100.00')
        )
        # Refresh from DB to update balance/paid_amount
        rec.recalculate_from_payments()
        self.assertEqual(rec.paid_amount, Decimal('100.00'))
        self.assertEqual(self.rental.receivables.count(), 1)

        # Call the generate view to split into 3 installments
        url = reverse('billing:generate', args=[self.rental.pk])
        response = self.client.post(url, {
            'installments': 3,
            'first_due_date': '2026-07-01'
        })
        self.assertEqual(response.status_code, 302)

        # Validate that the generation was blocked and the old receivable is still there
        self.assertEqual(self.rental.receivables.count(), 1)
        self.assertTrue(Receivable.objects.filter(pk=rec.pk).exists())

