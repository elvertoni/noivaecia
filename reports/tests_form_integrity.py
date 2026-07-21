from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from customers.models import Customer
from rentals.models import Rental


User = get_user_model()


class ReportFilterIntegrityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='report-forms@test.com', password='pass')
        ModulePermission.objects.create(user=self.user, module_key='reports', allowed=True)
        self.client.force_login(self.user)
        customer = Customer.objects.create(name='Beatriz Lima')
        self.rental = Rental.objects.create(
            number=3001,
            customer=customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            total_value=Decimal('100.00'),
            status=Rental.Status.PENDING,
        )

    def test_report_accepts_brazilian_date_filters_and_re_renders_iso_values(self):
        response = self.client.get(
            reverse('reports:a_retirar'),
            {'date_from': '10/06/2026', 'date_to': '10/06/2026'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '#3001')
        self.assertEqual(response.context['date_from'], '2026-06-10')
        self.assertEqual(response.context['date_to'], '2026-06-10')

    def test_invalid_date_filter_never_exports_the_unfiltered_report(self):
        response = self.client.get(
            reverse('reports:a_retirar'), {'date_from': '31/02/2026'},
        )

        self.assertNotContains(response, '#3001')
