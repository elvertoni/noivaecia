from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from accounts.models import ModulePermission
from catalog.models import Category, Product
from customers.models import Customer
from rentals.models import Rental, RentalItem

User = get_user_model()


def make_uploaded_image(name='comprovante.png', size=(2200, 1000), image_format='PNG'):
    buffer = BytesIO()
    Image.new('RGB', size, color=(240, 240, 240)).save(buffer, format=image_format)
    buffer.seek(0)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=f'image/{image_format.lower()}',
    )


class RentalModelTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.category = Category.objects.create(prefix='VN', name='Vestidos')
        self.p1 = Product.objects.create(category=self.category, code=1, description='A', value=300)
        self.p2 = Product.objects.create(category=self.category, code=2, description='B', value=150)

    def test_recalculate_total_sums_items(self):
        rental = Rental.objects.create(
            number=1, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
        )
        RentalItem.objects.create(rental=rental, product=self.p1, value=Decimal('300'))
        RentalItem.objects.create(rental=rental, product=self.p2, value=Decimal('150'))
        rental.recalculate_total()
        self.assertEqual(rental.total_value, Decimal('450'))

    def test_default_status_is_pending(self):
        rental = Rental.objects.create(
            number=2, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
        )
        self.assertEqual(rental.status, Rental.Status.PENDING)

    def test_timestamps_present(self):
        rental = Rental.objects.create(
            number=3, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
        )
        self.assertIsNotNone(rental.created_at)
        self.assertIsNotNone(rental.updated_at)


class RentalCreateFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='rentals', allowed=True)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Maria')
        cat = Category.objects.create(prefix='VN', name='Vestidos')
        self.product = Product.objects.create(category=cat, code=1, description='A', value=300)

    def test_create_rental_generates_sequential_number_and_total(self):
        data = {
            'customer': self.customer.pk,
            'pickup_date': '2026-06-10',
            'return_date': '2026-06-15',
            'penalty_value': '0',
            'notes': '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-description': 'Branco M',
            'items-0-value': '300',
            'items-0-proof_photo_upload': make_uploaded_image(),
            'items-0-DELETE': '',
        }
        response = self.client.post('/locacoes/nova/', data)
        self.assertEqual(response.status_code, 302)
        rental = Rental.objects.get()
        self.assertEqual(rental.number, 1)
        self.assertEqual(rental.items.count(), 1)
        self.assertEqual(rental.total_value, Decimal('300'))
        item = rental.items.get()
        self.assertTrue(item.has_proof_photo)
        self.assertEqual(item.proof_photo_content_type, 'image/jpeg')
        self.assertLessEqual(max(item.proof_photo_width, item.proof_photo_height), 1600)

        response = self.client.get(f'/locacoes/itens/{item.pk}/foto/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
        self.assertGreater(len(response.content), 0)

    def test_rental_requires_module_permission(self):
        other = User.objects.create_user(email='no@b.com', password='Senha12345')
        self.client.force_login(other)
        self.assertEqual(self.client.get('/locacoes/').status_code, 403)
