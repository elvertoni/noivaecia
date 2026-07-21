from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ActionPermission, ModulePermission
from billing.forms import ManualMovementForm, PaymentForm
from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from company.models import Company
from customers.models import Customer
from rentals.models import Rental


User = get_user_model()


class BillingFormIntegrityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='billing-forms@test.com', password='pass')
        ModulePermission.objects.create(user=self.user, module_key='billing', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='billing.receive', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='billing.cash', allowed=True)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Maria Silva')
        self.rental = Rental.objects.create(
            number=1001,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            total_value=Decimal('100.00'),
        )
        self.receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=date(2026, 6, 20),
            amount=Decimal('100.00'),
        )
        self.account = CashAccount.objects.create(name='Caixa principal')

    def test_payment_form_accepts_brazilian_money_and_rejects_zero(self):
        valid = PaymentForm(data={'value': 'R$ 1.234,56', 'payment_date': '20/06/2026'})
        self.assertTrue(valid.is_valid())
        self.assertEqual(valid.cleaned_data['value'], Decimal('1234.56'))
        self.assertEqual(valid.cleaned_data['payment_date'], date(2026, 6, 20))

        invalid = PaymentForm(data={'value': '0', 'payment_date': '2026-06-20'})
        self.assertFalse(invalid.is_valid())
        self.assertIn('value', invalid.errors)

    def test_manual_movement_requires_an_exact_customer_match(self):
        partial = ManualMovementForm(data={
            'date': '2026-06-20',
            'account': self.account.pk,
            'direction': 'inflow',
            'amount': '10,00',
            'description': 'Ajuste',
            'customer_name': 'Maria',
        })
        self.assertFalse(partial.is_valid())
        self.assertIn('customer_name', partial.errors)

        exact = ManualMovementForm(data={
            'date': '2026-06-20',
            'account': self.account.pk,
            'direction': 'inflow',
            'amount': '10,00',
            'description': 'Ajuste',
            'customer_name': 'Maria Silva',
        })
        self.assertTrue(exact.is_valid())
        self.assertEqual(exact.cleaned_data['customer'], self.customer)

    def test_multi_pay_rejects_an_amount_above_selected_balance(self):
        response = self.client.post(
            reverse('billing:multi_pay', args=[self.customer.pk]),
            {
                'total_amount': '101,00',
                'payment_date': '20/06/2026',
                'method': 'cash',
                'notes': '',
                'receivable_ids': [self.receivable.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'maior que o saldo dos títulos selecionados')
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(FinancialMovement.objects.count(), 0)

    def test_invalid_installment_generation_keeps_field_errors_on_screen(self):
        response = self.client.post(
            reverse('billing:generate', args=[self.rental.pk]),
            {'installments': '0', 'first_due_date': '20/06/2026'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('installments', response.context['generate_form'].errors)
        self.assertContains(response, 'Certifique-se que este valor seja maior ou igual a 1.')

    def test_legacy_payment_uses_audited_payment_service(self):
        response = self.client.post(
            reverse('billing:pay', args=[self.receivable.pk]),
            {'value': '40,00', 'payment_date': '20/06/2026'},
        )

        self.assertRedirects(response, reverse('billing:list', args=[self.rental.pk]))
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(FinancialMovement.objects.count(), 1)
        self.receivable.refresh_from_db()
        self.assertEqual(self.receivable.balance, Decimal('60.00'))

    def test_receivable_payment_does_not_redirect_to_an_external_next_url(self):
        Company.load()
        response = self.client.post(
            reverse('billing:pay_receivable', args=[self.receivable.pk]),
            {
                'amount': '100,00',
                'payment_date': '20/06/2026',
                'method': 'cash',
                'interest_amount': '0',
                'discount_amount': '0',
                'notes': '',
                'next': 'https://invalid.example/',
            },
        )

        self.assertRedirects(
            response,
            reverse('billing:customer_receivables', args=[self.customer.pk]),
        )

    def test_payment_report_filters_brazilian_date_input_and_re_renders_iso_value(self):
        Payment.objects.create(
            receivable=self.receivable,
            customer=self.customer,
            rental=self.rental,
            payment_date=date(2026, 6, 20),
            amount=Decimal('25.00'),
            method='cash',
            user=self.user,
        )

        response = self.client.get(
            reverse('billing:payment_report'),
            {'date_from': '20/06/2026'},
        )

        self.assertEqual(response.context['total_received'], Decimal('25.00'))
        self.assertEqual(response.context['filters']['date_from'], '2026-06-20')

    def test_invalid_payment_report_date_never_widens_the_result_set(self):
        Payment.objects.create(
            receivable=self.receivable,
            customer=self.customer,
            rental=self.rental,
            payment_date=date(2026, 6, 20),
            amount=Decimal('25.00'),
            method='cash',
            user=self.user,
        )

        response = self.client.get(
            reverse('billing:payment_report'), {'date_from': '31/02/2026'},
        )

        self.assertEqual(response.context['total_received'], Decimal('0'))
