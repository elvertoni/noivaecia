from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import ModulePermission
from catalog.availability import find_rental_for
from catalog.models import Category, Product
from customers.models import Customer
from rentals.models import Rental, RentalItem

User = get_user_model()


class CatalogModelTests(TestCase):
    def test_category_str(self):
        category = Category.objects.create(prefix='VN', name='Vestidos')
        self.assertEqual(str(category), 'VN · Vestidos')

    def test_product_allows_legacy_duplicate_codes(self):
        category = Category.objects.create(prefix='VN', name='Vestidos')
        Product.objects.create(category=category, code=1, description='A', value=10)
        Product.objects.create(category=category, code=1, description='B', value=20)
        self.assertEqual(Product.objects.filter(category=category, code=1).count(), 2)


class AvailabilityTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.category = Category.objects.create(prefix='VN', name='Vestidos')
        self.product = Product.objects.create(category=self.category, code=1, description='A', value=300)
        self.rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 20),
            status=Rental.Status.PICKED_UP,
        )
        RentalItem.objects.create(rental=self.rental, product=self.product, value=300)

    def test_rented_inside_window(self):
        self.assertEqual(find_rental_for(self.product, date(2026, 6, 15)), self.rental)

    def test_available_outside_window(self):
        self.assertIsNone(find_rental_for(self.product, date(2026, 6, 25)))

    def test_available_after_returned(self):
        self.rental.status = Rental.Status.RETURNED
        self.rental.save()
        self.assertIsNone(find_rental_for(self.product, date(2026, 6, 15)))


class ModulePermissionAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        self.client.force_login(self.user)

    def test_denied_without_permission(self):
        self.assertEqual(self.client.get('/catalogo/produtos/').status_code, 403)

    def test_allowed_with_permission(self):
        ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.assertEqual(self.client.get('/catalogo/produtos/').status_code, 200)

    def test_revoked_permission_denies(self):
        perm = ModulePermission.objects.create(user=self.user, module_key='catalog', allowed=True)
        self.assertEqual(self.client.get('/catalogo/produtos/').status_code, 200)
        perm.allowed = False
        perm.save()
        self.assertEqual(self.client.get('/catalogo/produtos/').status_code, 403)
