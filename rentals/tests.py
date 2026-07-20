from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import Mock

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
from rentals.signals import sync_rental_total

User = get_user_model()


class RentalItemSignalTests(TestCase):
    def test_fixture_load_skips_total_recalculation(self):
        rental = Mock()
        item = Mock(rental=rental)

        sync_rental_total(RentalItem, item, raw=True)

        rental.recalculate_total.assert_not_called()


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


class RentalItemAvailabilityTests(TestCase):
    """Server-side double-booking guard + add-item-by-number entry (R7.03/R7.04)."""

    def setUp(self):
        self.user = User.objects.create_user(email='u@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='rentals', allowed=True)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Maria')
        self.other_customer = Customer.objects.create(name='Joana')
        cat = Category.objects.create(prefix='VN', name='Vestidos')
        self.product = Product.objects.create(category=cat, code=1, description='A', value=300)
        # Existing active rental holding the product over an overlapping window.
        self.existing = Rental.objects.create(
            number=50, customer=self.other_customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
        )
        RentalItem.objects.create(rental=self.existing, product=self.product, value=Decimal('300'))

    def _create_payload(self, pickup, return_d):
        return {
            'customer': self.customer.pk,
            'pickup_date': pickup,
            'return_date': return_d,
            'penalty_value': '0',
            'notes': '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-description': '',
            'items-0-value': '300',
            'items-0-DELETE': '',
        }

    def test_overlapping_booking_is_blocked(self):
        response = self.client.post(
            '/locacoes/nova/', self._create_payload('2026-06-12', '2026-06-18')
        )
        self.assertEqual(response.status_code, 200)  # re-rendered, not saved
        self.assertContains(response, 'já está alocada na locação #50')
        self.assertEqual(Rental.objects.exclude(pk=self.existing.pk).count(), 0)

    def test_non_overlapping_booking_is_allowed(self):
        response = self.client.post(
            '/locacoes/nova/', self._create_payload('2026-07-01', '2026-07-05')
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Rental.objects.exclude(pk=self.existing.pk).count(), 1)

    def test_add_item_entry_redirects_to_update(self):
        response = self.client.get(
            reverse('rentals:add_item_entry'), {'number': '50'}
        )
        self.assertRedirects(
            response,
            f"{reverse('rentals:update', args=[self.existing.pk])}?add=1",
            fetch_redirect_response=False,
        )

    def test_add_item_entry_unknown_number_redirects_to_list(self):
        response = self.client.get(
            reverse('rentals:add_item_entry'), {'number': '9999'}
        )
        self.assertRedirects(response, reverse('rentals:list'))


class RentalItemEditingTests(TestCase):
    """Item loading / persistence rules on the rental edit screen."""

    def setUp(self):
        self.user = User.objects.create_user(email='edit@b.com', password='Senha12345')
        ModulePermission.objects.create(user=self.user, module_key='rentals', allowed=True)
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Maria')
        cat = Category.objects.create(prefix='VN', name='Vestidos')
        self.p1 = Product.objects.create(category=cat, code=1, description='A', value=300)
        self.p2 = Product.objects.create(category=cat, code=2, description='B', value=150)
        self.p3 = Product.objects.create(category=cat, code=3, description='C', value=200)

    def _rental_with_items(self, products, number=100):
        rental = Rental.objects.create(
            number=number, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            penalty_value=Decimal('0'),
        )
        items = [
            RentalItem.objects.create(rental=rental, product=p, value=p.value)
            for p in products
        ]
        return rental, items

    def _base_payload(self, **extra):
        data = {
            'customer': self.customer.pk,
            'use_for': '',
            'pickup_date': '2026-06-10',
            'return_date': '2026-06-15',
            'penalty_value': '0',
            'notes': '',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
        }
        data.update(extra)
        return data

    def test_three_item_rental_loads_exactly_three_forms(self):
        rental, _ = self._rental_with_items([self.p1, self.p2, self.p3])
        response = self.client.get(f'/locacoes/{rental.pk}/editar/')
        self.assertEqual(response.status_code, 200)
        items = response.context['items']
        self.assertEqual(items.initial_form_count(), 3)
        # extra=0 → no blank trailing form is rendered.
        self.assertEqual(len(items.forms), 3)

    def test_blank_appended_form_creates_no_record(self):
        rental, items = self._rental_with_items([self.p1])
        item = items[0]
        response = self.client.post(f'/locacoes/{rental.pk}/editar/', self._base_payload(
            **{
                'items-TOTAL_FORMS': '2',
                'items-INITIAL_FORMS': '1',
                'items-0-id': item.pk,
                'items-0-product': self.p1.pk,
                'items-0-description': 'A',
                'items-0-value': '300',
                'items-0-DELETE': '',
                # Second form left entirely blank — must be ignored.
                'items-1-id': '',
                'items-1-product': '',
                'items-1-description': '',
                'items-1-value': '',
                'items-1-DELETE': '',
            }
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(rental.items.count(), 1)

    def test_duplicate_product_is_blocked(self):
        data = {
            'customer': self.customer.pk,
            'use_for': '',
            'pickup_date': '2026-07-01',
            'return_date': '2026-07-05',
            'penalty_value': '0',
            'notes': '',
            'items-TOTAL_FORMS': '2',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.p1.pk,
            'items-0-description': '',
            'items-0-value': '300',
            'items-0-DELETE': '',
            'items-1-product': self.p1.pk,
            'items-1-description': '',
            'items-1-value': '300',
            'items-1-DELETE': '',
        }
        response = self.client.post('/locacoes/nova/', data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'já foi adicionada')
        self.assertEqual(Rental.objects.count(), 0)

    def test_preexisting_duplicate_product_stays_editable(self):
        # Legacy rentals may already hold the same product twice; editing them
        # must still save (only *new* duplicates are blocked).
        rental, items = self._rental_with_items([self.p1, self.p1], number=120)
        a, b = items
        response = self.client.post(f'/locacoes/{rental.pk}/editar/', self._base_payload(
            **{
                'items-TOTAL_FORMS': '2',
                'items-INITIAL_FORMS': '2',
                'items-0-id': a.pk,
                'items-0-product': self.p1.pk,
                'items-0-value': '300',
                'items-0-DELETE': '',
                'items-1-id': b.pk,
                'items-1-product': self.p1.pk,
                'items-1-value': '300',
                'items-1-DELETE': '',
            }
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(rental.items.count(), 2)

    def test_remove_intermediate_item_preserves_other_ids(self):
        rental, items = self._rental_with_items([self.p1, self.p2, self.p3])
        first, middle, last = items
        response = self.client.post(f'/locacoes/{rental.pk}/editar/', self._base_payload(
            **{
                'items-TOTAL_FORMS': '3',
                'items-INITIAL_FORMS': '3',
                'items-0-id': first.pk,
                'items-0-product': self.p1.pk,
                'items-0-value': '300',
                'items-0-DELETE': '',
                'items-1-id': middle.pk,
                'items-1-product': self.p2.pk,
                'items-1-value': '150',
                'items-1-DELETE': 'on',
                'items-2-id': last.pk,
                'items-2-product': self.p3.pk,
                'items-2-value': '200',
                'items-2-DELETE': '',
            }
        ))
        self.assertEqual(response.status_code, 302)
        remaining = list(rental.items.order_by('pk').values_list('pk', flat=True))
        self.assertEqual(remaining, [first.pk, last.pk])
        self.assertFalse(RentalItem.objects.filter(pk=middle.pk).exists())

    def test_blank_unsaved_row_not_rendered_after_validation_error(self):
        # return_date <= pickup_date forces a header error; a trailing blank
        # item row in the POST must NOT be re-rendered.
        data = {
            'customer': self.customer.pk,
            'use_for': '',
            'pickup_date': '2026-07-05',
            'return_date': '2026-07-01',  # invalid: before pickup
            'penalty_value': '0',
            'notes': '',
            'items-TOTAL_FORMS': '2',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.p1.pk,
            'items-0-description': 'A',
            'items-0-value': '300',
            'items-0-DELETE': '',
            'items-1-product': '',
            'items-1-description': '',
            'items-1-value': '',
            'items-1-DELETE': '',
        }
        response = self.client.post('/locacoes/nova/', data)
        self.assertEqual(response.status_code, 200)
        # Filled row 0 is re-rendered; blank row 1 is suppressed.
        self.assertContains(response, 'name="items-0-product"')
        self.assertNotContains(response, 'name="items-1-product"')
