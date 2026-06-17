from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from accounts.models import ActionPermission, ModulePermission
from catalog.models import Category, Product
from customers.models import Customer
from movements.models import Pickup
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

    def test_report_indexes_declared(self):
        index_names = {index.name for index in Rental._meta.indexes}

        self.assertIn('rental_customer_status_idx', index_names)
        self.assertIn('rental_status_pickup_num_idx', index_names)
        self.assertIn('rental_status_return_num_idx', index_names)
        self.assertIn('rental_customer_pickup_idx', index_names)


class RentalCreateFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='rentals', allowed=True)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Maria')
        cat = Category.objects.create(prefix='VN', name='Vestidos')
        self.product = Product.objects.create(category=cat, code=1, description='A', value=300)
        self.other_product = Product.objects.create(category=cat, code=2, description='B', value=150)

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
        # Proof photo is served as a streaming FileResponse.
        self.assertGreater(len(b''.join(response.streaming_content)), 0)

    def test_clear_rental_item_proof_photo(self):
        import os
        # First, create a rental with an item that has a photo
        rental = Rental.objects.create(
            number=5,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            penalty_value=Decimal('0'),
        )
        item = RentalItem.objects.create(
            rental=rental,
            product=self.product,
            description='Branco M',
            value=Decimal('300'),
        )
        # Save a file to proof_photo
        item.proof_photo.save('comprovante.jpg', make_uploaded_image())
        item.proof_photo_content_type = 'image/jpeg'
        item.proof_photo_filename = 'comprovante.jpg'
        item.proof_photo_size = 100
        item.proof_photo_width = 100
        item.proof_photo_height = 100
        item.save()

        self.assertTrue(item.has_proof_photo)
        file_path = item.proof_photo.path
        self.assertTrue(os.path.exists(file_path))

        # Now update the rental and clear the photo
        response = self.client.post(f'/locacoes/{rental.pk}/editar/', {
            'customer': self.customer.pk,
            'use_for': '',
            'pickup_date': '2026-06-10',
            'return_date': '2026-06-15',
            'penalty_value': '0',
            'notes': '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item.pk,
            'items-0-product': self.product.pk,
            'items-0-description': 'Branco M',
            'items-0-value': '300',
            'items-0-proof_photo_upload-clear': 'on',
            'items-0-DELETE': '',
        })

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.has_proof_photo)
        self.assertEqual(item.proof_photo.name, '')
        self.assertEqual(item.proof_photo_size, 0)
        self.assertEqual(item.proof_photo_width, 0)
        self.assertEqual(item.proof_photo_height, 0)
        self.assertEqual(item.proof_photo_content_type, '')
        self.assertEqual(item.proof_photo_filename, '')
        self.assertFalse(os.path.exists(file_path))

    def test_rental_requires_module_permission(self):
        other = User.objects.create_user(email='no@b.com', password='Senha12345')
        self.client.force_login(other)
        self.assertEqual(self.client.get('/locacoes/').status_code, 403)

    def test_update_rental_can_change_item_product(self):
        rental = Rental.objects.create(
            number=10,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            penalty_value=Decimal('0'),
        )
        item = RentalItem.objects.create(
            rental=rental,
            product=self.product,
            description='Branco M',
            value=Decimal('300'),
        )

        response = self.client.post(f'/locacoes/{rental.pk}/editar/', {
            'customer': self.customer.pk,
            'use_for': '',
            'pickup_date': '2026-06-10',
            'return_date': '2026-06-15',
            'penalty_value': '0',
            'notes': '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item.pk,
            'items-0-product': self.other_product.pk,
            'items-0-description': 'Preto P',
            'items-0-value': '150',
            'items-0-DELETE': '',
        })

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        rental.refresh_from_db()
        self.assertEqual(item.product_id, self.other_product.pk)
        self.assertEqual(rental.total_value, Decimal('150'))


class RentalCancelledStatusTests(TestCase):
    """R3.07, R3.08, R3.09 — cancelled status, use_for and cancellation fields."""

    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')
        self.user = User.objects.create_user(email='op@b.com', password='Senha12345')

    def test_cancelled_is_valid_status(self):
        rental = Rental.objects.create(
            number=10, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            status=Rental.Status.CANCELLED,
        )
        self.assertEqual(rental.status, 'cancelled')

    def test_use_for_field_stores_event(self):
        rental = Rental.objects.create(
            number=11, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            use_for='Formatura UFMG',
        )
        rental.refresh_from_db()
        self.assertEqual(rental.use_for, 'Formatura UFMG')

    def test_cancellation_fields_nullable_by_default(self):
        rental = Rental.objects.create(
            number=12, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
        )
        self.assertIsNone(rental.cancelled_at)
        self.assertIsNone(rental.cancelled_by)
        self.assertEqual(rental.cancelled_reason, '')

    def test_cancel_stores_reason_and_user(self):
        from django.utils import timezone
        rental = Rental.objects.create(
            number=13, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
        )
        now = timezone.now()
        rental.status = Rental.Status.CANCELLED
        rental.cancelled_reason = 'Cliente desistiu'
        rental.cancelled_at = now
        rental.cancelled_by = self.user
        rental.save()
        rental.refresh_from_db()
        self.assertEqual(rental.cancelled_reason, 'Cliente desistiu')
        self.assertEqual(rental.cancelled_by, self.user)

    def test_legacy_notes_stored(self):
        rental = Rental.objects.create(
            number=14, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            legacy_notes='locado.obs: usar em debutante',
        )
        rental.refresh_from_db()
        self.assertIn('debutante', rental.legacy_notes)


class RentalDeleteViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='delete@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='rentals', allowed=True)
        ActionPermission.objects.create(user=self.user, action_key='rentals.delete', allowed=True)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Maria')

    def test_cancelled_rental_with_pickup_cannot_be_deleted(self):
        rental = Rental.objects.create(
            number=30,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
            status=Rental.Status.CANCELLED,
        )
        Pickup.objects.create(rental=rental, pickup_date=date(2026, 6, 10))
        Rental.objects.filter(pk=rental.pk).update(status=Rental.Status.CANCELLED)

        response = self.client.post(reverse('rentals:delete', args=[rental.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Rental.objects.filter(pk=rental.pk).exists())
