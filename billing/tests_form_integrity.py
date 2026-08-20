import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ActionPermission, ModulePermission
from billing.forms import ManualMovementForm, PaymentForm
from billing.models import (
    CashAccount,
    FinancialMovement,
    Payment,
    Receivable,
    Receipt,
)
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

    def test_payment_and_manual_movement_reject_future_dates(self):
        future = timezone.localdate().replace(
            year=timezone.localdate().year + 1,
        )
        payment = PaymentForm(data={
            'value': '10,00',
            'payment_date': future.isoformat(),
        })
        movement = ManualMovementForm(data={
            'date': future.isoformat(),
            'account': self.account.pk,
            'direction': 'inflow',
            'amount': '10,00',
            'description': 'Futuro',
            'customer_name': '',
        })

        self.assertFalse(payment.is_valid())
        self.assertFalse(movement.is_valid())
        self.assertIn('payment_date', payment.errors)
        self.assertIn('date', movement.errors)

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
        self.assertEqual(Receipt.objects.count(), 0)
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

    def test_partial_receipt_message_names_the_remaining_balance(self):
        """The reported scenario: R$ 250 installment received short at R$ 150."""
        title = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 6, 20), amount=Decimal('250.00')
        )

        short = self.client.post(
            reverse('billing:pay_receivable', args=[title.pk]),
            {
                'amount': '150,00',
                'payment_date': '20/06/2026',
                'method': 'cash',
                'submission_token': str(uuid.uuid4()),
            },
            follow=True,
        )
        self.assertIn(
            'ainda restam R$ 100,00 nesta mesma parcela',
            str(list(short.context['messages'])[0]),
        )

        rest = self.client.post(
            reverse('billing:pay_receivable', args=[title.pk]),
            {
                'amount': '100,00',
                'payment_date': '20/06/2026',
                'method': 'cash',
                'submission_token': str(uuid.uuid4()),
            },
            follow=True,
        )
        self.assertIn(
            'parcela quitada', str(list(rest.context['messages'])[0])
        )
        title.refresh_from_db()
        self.assertEqual(title.paid_amount, Decimal('250.00'))
        self.assertEqual(title.balance, Decimal('0.00'))

    def test_untouched_generate_form_rewrites_nothing(self):
        """A blind submit must fail validation, not collapse the plan into one."""
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 7, 10), amount=Decimal('40.00')
        )
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 8, 10), amount=Decimal('60.00')
        )
        before = set(self.rental.receivables.values_list('pk', flat=True))

        response = self.client.post(
            reverse('billing:generate', args=[self.rental.pk]), {}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('installments', response.context['generate_form'].errors)
        self.assertEqual(
            set(self.rental.receivables.values_list('pk', flat=True)), before
        )

    def test_generate_form_has_no_prefilled_installment_count(self):
        response = self.client.get(reverse('billing:list', args=[self.rental.pk]))

        self.assertIsNone(response.context['generate_form']['installments'].value())

    def test_reprocess_panel_confirms_and_names_its_effect(self):
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 7, 10), amount=Decimal('100.00')
        )

        response = self.client.get(reverse('billing:list', args=[self.rental.pk]))

        self.assertContains(response, 'data-confirm=')
        self.assertContains(response, 'Reorganizar parcelas')
        self.assertNotContains(response, 'Atualizar parcelas')

    def test_reprocess_panel_hidden_when_nothing_to_reschedule(self):
        """Half the production clicks landed on this branch as a failed POST."""
        paid = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 7, 10), amount=Decimal('100.00')
        )
        Payment.objects.create(
            receivable=paid, payment_date=date(2026, 7, 10), amount=Decimal('100.00')
        )
        paid.recalculate_from_payments()

        response = self.client.get(reverse('billing:list', args=[self.rental.pk]))

        self.assertFalse(response.context['reprocess_preview']['can_reprocess'])
        self.assertContains(response, 'Não há saldo sem histórico financeiro')
        self.assertNotContains(response, 'data-confirm=')

    def test_reprocess_success_message_discloses_what_changed(self):
        partial = Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 4, 10), amount=Decimal('70.00')
        )
        Payment.objects.create(
            receivable=partial, payment_date=date(2026, 4, 10), amount=Decimal('20.00')
        )
        partial.recalculate_from_payments()
        Receivable.objects.create(
            rental=self.rental, due_date=date(2026, 5, 10), amount=Decimal('30.00')
        )

        response = self.client.post(
            reverse('billing:generate', args=[self.rental.pk]),
            {'installments': '2', 'first_due_date': '01/05/2026'},
            follow=True,
        )

        message = str(list(response.context['messages'])[0])
        self.assertIn('2 parcela(s) futura(s) criada(s)', message)
        # ``self.receivable`` from setUp plus the one created above.
        self.assertIn('2 título(s) sem recebimento substituído(s)', message)
        self.assertIn('ajustado de R$ 70.00 para R$ 20.00', message)
        self.assertIn('saldo de R$ 50.00 entrou no novo parcelamento', message)

    def test_installment_generation_requires_receive_action(self):
        ActionPermission.objects.filter(
            user=self.user,
            action_key='billing.receive',
        ).update(allowed=False)

        response = self.client.post(
            reverse('billing:generate', args=[self.rental.pk]),
            {'installments': '1', 'first_due_date': '20/06/2026'},
        )

        self.assertEqual(response.status_code, 403)

    def test_legacy_payment_uses_audited_payment_service(self):
        response = self.client.post(
            reverse('billing:pay', args=[self.receivable.pk]),
            {'value': '40,00', 'payment_date': '20/06/2026'},
        )

        self.assertRedirects(response, reverse('billing:list', args=[self.rental.pk]))
        self.assertEqual(Receipt.objects.count(), 1)
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
