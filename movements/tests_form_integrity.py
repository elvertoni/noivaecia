from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from billing.models import Receivable
from customers.models import Customer
from movements.forms import ReturnForm
from movements.models import Pickup, Return
from rentals.models import Rental


User = get_user_model()


class ReturnFormIntegrityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='return-forms@test.com', password='pass')
        ModulePermission.objects.create(user=self.user, module_key='movements', allowed=True)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Ana Souza')
        self.rental = Rental.objects.create(
            number=2001,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            status=Rental.Status.PICKED_UP,
            total_value=Decimal('100.00'),
        )
        self.pickup = Pickup.objects.create(
            rental=self.rental,
            pickup_date=date(2026, 6, 11),
        )

    def test_return_date_cannot_precede_registered_pickup(self):
        form = ReturnForm(
            data={'return_date': '10/06/2026'},
            rental=self.rental,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('return_date', form.errors)

    def test_optional_payment_method_requires_a_positive_amount(self):
        form = ReturnForm(
            data={
                'return_date': '15/06/2026',
                'payment_amount': '',
                'payment_method': 'cash',
                'payment_date': '15/06/2026',
            },
            rental=self.rental,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('payment_amount', form.errors)

    def test_return_rejects_payment_above_available_balance_before_saving(self):
        receivable = Receivable.objects.create(
            rental=self.rental,
            due_date=date(2026, 6, 15),
            amount=Decimal('100.00'),
        )

        response = self.client.post(
            reverse('movements:return', args=[self.rental.pk]),
            {
                'return_date': '15/06/2026',
                'payment_amount': '101,00',
                'payment_method': 'cash',
                'payment_date': '15/06/2026',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'maior que o saldo em aberto')
        self.assertFalse(Return.objects.filter(rental=self.rental).exists())
        receivable.refresh_from_db()
        self.assertEqual(receivable.balance, Decimal('100.00'))

    def test_return_list_filters_brazilian_date_input_and_re_renders_iso_value(self):
        response = self.client.get(
            reverse('movements:return_list'),
            {'date_from': '11/06/2026', 'date_to': '11/06/2026'},
        )

        self.assertContains(response, '#2001')
        self.assertEqual(response.context['date_from'], '2026-06-11')
        self.assertEqual(response.context['date_to'], '2026-06-11')

    def test_invalid_return_list_date_never_widens_the_result_set(self):
        response = self.client.get(
            reverse('movements:return_list'), {'date_from': '31/02/2026'},
        )

        self.assertNotContains(response, '#2001')
