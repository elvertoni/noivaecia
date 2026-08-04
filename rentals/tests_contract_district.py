from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import ModulePermission
from company.models import Company
from customers.models import Customer
from rentals.models import Rental

User = get_user_model()


class RentalContractDistrictTests(TestCase):
    """Verifies that customer district (bairro) is displayed on the printed contract."""

    def setUp(self):
        user = User.objects.create_user(
            email='contrato-bairro@noivasecia.test',
            password='Senha12345',
        )
        ModulePermission.objects.create(user=user, module_key='rentals', allowed=True)
        self.client.force_login(user)
        Company.load()

    def test_printed_contract_shows_customer_district(self):
        customer = Customer.objects.create(
            name='João da Silva',
            address='Rua das Flores, 123',
            district='Jardim Brasil',
            city='Bandeirantes',
        )
        rental = Rental.objects.create(
            number=9901,
            customer=customer,
            pickup_date=date(2026, 9, 10),
            return_date=date(2026, 9, 15),
            total_value=Decimal('300.00'),
        )

        response = self.client.get(reverse('rentals:contract', args=[rental.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn('<span class="field-caption">Bairro</span>', content)
        self.assertIn('Jardim Brasil', content)
