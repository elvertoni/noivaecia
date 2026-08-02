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
from billing.models import CashAccount, Payment, Receivable
from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer
from movements.models import Pickup, Return
from rentals.forms import RentalForm, RentalItemForm
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

    def test_final_value_without_discount_equals_total(self):
        rental = Rental.objects.create(
            number=100, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            total_value=Decimal('450'),
        )
        self.assertEqual(rental.final_value, Decimal('450'))
        self.assertEqual(rental.discount_amount, Decimal('0.00'))

    def test_final_value_with_cash_discount_applies_10_percent(self):
        rental = Rental.objects.create(
            number=101, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            total_value=Decimal('450'), cash_discount=True,
        )
        self.assertEqual(rental.final_value, Decimal('405.00'))
        self.assertEqual(rental.discount_amount, Decimal('45.00'))

    def test_final_value_with_custom_cash_discount_percent(self):
        rental = Rental.objects.create(
            number=102, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            total_value=Decimal('450'), cash_discount=True,
            cash_discount_percent=Decimal('20'),
        )
        self.assertEqual(rental.final_value, Decimal('360.00'))
        self.assertEqual(rental.discount_amount, Decimal('90.00'))

    def test_final_value_with_custom_cash_discount_amount(self):
        rental = Rental.objects.create(
            number=103, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            total_value=Decimal('450'), cash_discount=True,
            cash_discount_amount=Decimal('120'),
        )
        self.assertEqual(rental.final_value, Decimal('330.00'))
        self.assertEqual(rental.discount_amount, Decimal('120.00'))

    def test_cash_discount_amount_never_exceeds_total(self):
        rental = Rental.objects.create(
            number=104, customer=self.customer,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            total_value=Decimal('100'), cash_discount=True,
            cash_discount_amount=Decimal('500'),
        )
        self.assertEqual(rental.final_value, Decimal('0.00'))
        self.assertEqual(rental.discount_amount, Decimal('100.00'))

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


class RentalFormValidationTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Maria')

    def _header_data(self, **overrides):
        data = {
            'customer': self.customer.pk,
            'pickup_date': '10/06/2026',
            'return_date': '15/06/2026',
            'penalty_value': '0,00',
            'notes': '',
        }
        data.update(overrides)
        return data

    def test_monetary_penalty_cannot_be_negative(self):
        form = RentalForm(data=self._header_data(penalty_value='-1,00'))

        self.assertFalse(form.is_valid())
        self.assertIn('penalty_value', form.errors)

    def test_down_payment_uses_only_payment_model_methods(self):
        form = RentalForm(data=self._header_data(
            installment_count='1',
            first_due_date='15/01/2027',
            down_payment_amount='100,00',
            down_payment_method='pix',
            down_payment_date='10/06/2026',
        ))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            {value for value, _label in form.fields['down_payment_method'].choices if value},
            set(Payment.Method.values),
        )

    def test_next_payment_may_match_down_payment_date(self):
        form = RentalForm(data=self._header_data(
            installment_count='1',
            first_due_date='30/07/2026',
            down_payment_amount='300,00',
            down_payment_method=Payment.Method.PIX,
            down_payment_date='30/07/2026',
        ))

        self.assertTrue(form.is_valid(), form.errors)

    def test_cash_discount_rejects_percent_and_amount_together(self):
        form = RentalForm(data=self._header_data(
            cash_discount='on',
            cash_discount_percent='20',
            cash_discount_amount='50,00',
        ))

        self.assertFalse(form.is_valid())
        self.assertIn('cash_discount_amount', form.errors)

    def test_cash_discount_percent_above_100_is_rejected(self):
        form = RentalForm(data=self._header_data(
            cash_discount='on',
            cash_discount_percent='150',
        ))

        self.assertFalse(form.is_valid())
        self.assertIn('cash_discount_percent', form.errors)

    def test_cash_discount_amount_requires_checkbox(self):
        form = RentalForm(data=self._header_data(cash_discount_amount='50,00'))

        self.assertFalse(form.is_valid())
        self.assertIn('cash_discount', form.errors)

    def test_new_item_value_starts_blank_instead_of_zero(self):
        form = RentalItemForm()

        self.assertEqual(form['value'].value(), '')

    def test_rental_item_value_cannot_be_negative(self):
        category = Category.objects.create(prefix='VN', name='Vestidos')
        product = Product.objects.create(category=category, code=1, description='A', value=300)
        # Formsets provide a prefix, so bind this direct form the same way.
        form = RentalItemForm(data={
            'items-0-product': product.pk,
            'items-0-description': '',
            'items-0-value': '-1,00',
        }, prefix='items-0')
        self.assertFalse(form.is_valid())
        self.assertIn('value', form.errors)

    def test_new_rental_rejects_inactive_customer(self):
        self.customer.is_active = False
        self.customer.save(update_fields=['is_active', 'updated_at'])

        form = RentalForm(data=self._header_data())

        self.assertFalse(form.is_valid())
        self.assertIn('customer', form.errors)

    def test_existing_rental_keeps_inactive_customer_editable(self):
        rental = Rental.objects.create(
            number=90,
            customer=self.customer,
            pickup_date=date(2026, 6, 10),
            return_date=date(2026, 6, 15),
        )
        self.customer.is_active = False
        self.customer.save(update_fields=['is_active', 'updated_at'])

        form = RentalForm(data=self._header_data(), instance=rental)

        self.assertTrue(form.is_valid(), form.errors)


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

    def test_create_rental_records_entry_and_schedules_remaining_balance(self):
        CashAccount.objects.create(name='Caixa principal')
        response = self.client.post('/locacoes/nova/', {
            'customer': self.customer.pk,
            'pickup_date': '2027-01-15',
            'return_date': '2027-01-20',
            'penalty_value': '0',
            'notes': '',
            'installment_count': '1',
            'first_due_date': '2027-01-15',
            'down_payment_amount': '150,00',
            'down_payment_method': Payment.Method.PIX,
            'down_payment_date': '2026-07-27',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-description': 'Branco M',
            'items-0-value': '300,00',
            'items-0-DELETE': '',
        })

        self.assertEqual(response.status_code, 302)
        rental = Rental.objects.get()
        receivables = list(rental.receivables.order_by('due_date', 'pk'))
        self.assertEqual(len(receivables), 2)
        self.assertEqual(receivables[0].amount, Decimal('150.00'))
        self.assertEqual(receivables[0].balance, Decimal('0.00'))
        self.assertEqual(receivables[1].amount, Decimal('150.00'))
        self.assertEqual(receivables[1].balance, Decimal('150.00'))
        self.assertEqual(receivables[1].due_date, date(2027, 1, 15))
        self.assertEqual(Payment.objects.count(), 1)

    def test_create_rental_accepts_payment_dates_on_same_day(self):
        CashAccount.objects.create(name='Caixa principal')
        response = self.client.post('/locacoes/nova/', {
            'customer': self.customer.pk,
            'pickup_date': '2027-01-15',
            'return_date': '2027-01-20',
            'penalty_value': '0',
            'notes': '',
            'installment_count': '1',
            'first_due_date': '2026-07-30',
            'down_payment_amount': '300,00',
            'down_payment_method': Payment.Method.PIX,
            'down_payment_date': '2026-07-30',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-description': 'Branco M',
            'items-0-value': '300,00',
            'items-0-DELETE': '',
        })

        self.assertEqual(response.status_code, 302)
        rental = Rental.objects.get()
        self.assertEqual(rental.receivables.count(), 1)
        self.assertEqual(rental.receivables.get().balance, Decimal('0.00'))

    def test_create_fully_paid_rental_without_future_due_date(self):
        CashAccount.objects.create(name='Caixa principal')
        response = self.client.post('/locacoes/nova/', {
            'customer': self.customer.pk,
            'pickup_date': '2027-01-15',
            'return_date': '2027-01-20',
            'penalty_value': '0',
            'notes': '',
            'down_payment_amount': '300,00',
            'down_payment_method': Payment.Method.PIX,
            'down_payment_date': '2026-07-30',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-description': 'Branco M',
            'items-0-value': '300,00',
            'items-0-DELETE': '',
        })

        self.assertEqual(response.status_code, 302)
        rental = Rental.objects.get()
        receivables = list(rental.receivables.all())
        self.assertEqual(len(receivables), 1)
        self.assertEqual(receivables[0].amount, Decimal('300.00'))
        self.assertEqual(receivables[0].balance, Decimal('0.00'))
        self.assertFalse(
            rental.receivables.filter(payments__isnull=True).exists()
        )

    def test_create_rental_rejects_entry_above_items_total(self):
        response = self.client.post('/locacoes/nova/', {
            'customer': self.customer.pk,
            'pickup_date': '2027-01-15',
            'return_date': '2027-01-20',
            'penalty_value': '0',
            'notes': '',
            'installment_count': '1',
            'first_due_date': '2027-01-15',
            'down_payment_amount': '300,01',
            'down_payment_method': Payment.Method.PIX,
            'down_payment_date': '2026-07-27',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-description': 'Branco M',
            'items-0-value': '300,00',
            'items-0-DELETE': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'O valor da entrada não pode superar o total da locação.')
        self.assertFalse(Rental.objects.exists())
        self.assertFalse(Receivable.objects.exists())
        self.assertFalse(Payment.objects.exists())

    def test_create_rental_attaches_due_date_error_to_first_due_date_field(self):
        response = self.client.post('/locacoes/nova/', {
            'customer': self.customer.pk,
            'pickup_date': '2027-01-15',
            'return_date': '2027-01-20',
            'penalty_value': '0',
            'notes': '',
            'installment_count': '1',
            'first_due_date': '2026-07-29',
            'down_payment_amount': '150,00',
            'down_payment_method': Payment.Method.PIX,
            'down_payment_date': '2026-07-30',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-description': 'Branco M',
            'items-0-value': '300,00',
            'items-0-DELETE': '',
        })

        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertIn('first_due_date', form.errors)
        self.assertContains(
            response,
            'O próximo vencimento não pode ser anterior à data da entrada.',
        )
        self.assertFalse(Rental.objects.exists())

    def test_create_requires_at_least_one_item(self):
        response = self.client.post('/locacoes/nova/', {
            'customer': self.customer.pk,
            'pickup_date': '2026-06-10',
            'return_date': '2026-06-15',
            'penalty_value': '0',
            'notes': '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': '',
            'items-0-description': '',
            'items-0-value': '',
            'items-0-DELETE': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Inclua ao menos uma peça na locação.')
        self.assertEqual(Rental.objects.count(), 0)

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
    """R3.07/R3.09 — cancelled status and cancellation fields."""

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

    def test_paid_rental_allows_notes_only_and_ignores_forged_contract_changes(self):
        rental, _ = self._rental_with_items([self.p1], number=140)
        rental.penalty_value = Decimal('50')
        rental.save()
        receivable = Receivable.objects.create(
            rental=rental,
            due_date=date(2026, 6, 15),
            amount=Decimal('300'),
        )
        Payment.objects.create(
            receivable=receivable,
            customer=self.customer,
            rental=rental,
            payment_date=date(2026, 6, 10),
            amount=Decimal('100'),
            method=Payment.Method.PIX,
            user=self.user,
        )
        other_customer = Customer.objects.create(name='Joana')

        response = self.client.get(reverse('rentals:update', args=[rental.pk]))
        self.assertTrue(response.context['form'].fields['pickup_date'].disabled)
        self.assertTrue(response.context['form'].fields['customer'].disabled)
        self.assertContains(response, 'Itens da locação')

        response = self.client.post(reverse('rentals:update', args=[rental.pk]), {
            'customer': other_customer.pk,
            'pickup_date': '2026-07-01',
            'return_date': '2026-07-05',
            'penalty_value': '999,99',
            'notes': 'Pagamento confirmado no balcão.',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-id': rental.items.get().pk,
            'items-0-product': self.p1.pk,
            'items-0-description': '',
            'items-0-value': '300',
            'items-0-DELETE': '',
        })

        self.assertRedirects(response, rental.get_absolute_url())
        rental.refresh_from_db()
        self.assertEqual(rental.customer, self.customer)
        self.assertEqual(rental.pickup_date, date(2026, 6, 10))
        self.assertEqual(rental.return_date, date(2026, 6, 15))
        self.assertEqual(rental.penalty_value, Decimal('50'))
        self.assertEqual(rental.notes, 'Pagamento confirmado no balcão.')

    def test_returned_rental_can_be_edited(self):
        rental, items = self._rental_with_items([self.p1], number=141)
        rental.status = Rental.Status.RETURNED
        rental.save(update_fields=['status', 'updated_at'])

        response = self.client.post(
            reverse('rentals:update', args=[rental.pk]),
            self._base_payload(**{
                'notes': 'Contrato conferido após a devolução.',
                'items-TOTAL_FORMS': '1',
                'items-INITIAL_FORMS': '1',
                'items-0-id': items[0].pk,
                'items-0-product': self.p1.pk,
                'items-0-description': '',
                'items-0-value': '300',
                'items-0-DELETE': '',
            }),
        )

        self.assertRedirects(response, rental.get_absolute_url())
        rental.refresh_from_db()
        self.assertEqual(rental.notes, 'Contrato conferido após a devolução.')

    def test_cancelled_rental_still_returns_404_when_edited(self):
        rental, _ = self._rental_with_items([self.p1], number=142)
        rental.status = Rental.Status.CANCELLED
        rental.save(update_fields=['status', 'updated_at'])

        response = self.client.get(reverse('rentals:update', args=[rental.pk]))

        self.assertEqual(response.status_code, 404)

    def test_paid_rental_item_changes_update_total_and_warn_about_installments(self):
        rental, items = self._rental_with_items([self.p1, self.p2], number=143)
        first, second = items
        Receivable.objects.create(
            rental=rental,
            due_date=date(2026, 6, 15),
            amount=Decimal('450'),
        )
        Payment.objects.create(
            receivable=rental.receivables.get(),
            customer=self.customer,
            rental=rental,
            payment_date=date(2026, 6, 10),
            amount=Decimal('100'),
            method=Payment.Method.PIX,
            user=self.user,
        )

        response = self.client.post(
            reverse('rentals:update', args=[rental.pk]),
            self._base_payload(**{
                'items-TOTAL_FORMS': '3',
                'items-INITIAL_FORMS': '2',
                'items-0-id': first.pk,
                'items-0-product': self.p1.pk,
                'items-0-description': '',
                'items-0-value': '300',
                'items-0-DELETE': 'on',
                'items-1-id': second.pk,
                'items-1-product': self.p2.pk,
                'items-1-description': '',
                'items-1-value': '150',
                'items-1-DELETE': '',
                'items-2-id': '',
                'items-2-product': self.p3.pk,
                'items-2-description': '',
                'items-2-value': '200',
                'items-2-DELETE': '',
            }),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        rental.refresh_from_db()
        self.assertEqual(rental.total_value, Decimal('350'))
        self.assertFalse(RentalItem.objects.filter(pk=first.pk).exists())
        self.assertTrue(rental.items.filter(product=self.p3).exists())
        self.assertContains(response, 'O total da locação foi alterado')
        self.assertContains(response, 'Revise ou gere novamente as parcelas futuras')


class RentalItemSnapshotTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='snapshot@example.com',
            password='Senha12345',
        )
        ModulePermission.objects.create(
            user=self.user,
            module_key='rentals',
            allowed=True,
        )
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Maria')
        self.category = Category.objects.create(prefix='VN', name='Vestidos')
        self.product = Product.objects.create(
            category=self.category,
            code=12,
            description='Vestido clássico',
            color='Azul',
            size='M',
            value=Decimal('300'),
        )
        self.rental = Rental.objects.create(
            number=200,
            customer=self.customer,
            pickup_date=date(2026, 8, 10),
            return_date=date(2026, 8, 15),
        )

    def test_contract_and_detail_keep_original_product_data_after_catalog_edit(self):
        item = RentalItem.objects.create(
            rental=self.rental,
            product=self.product,
            value=self.product.value,
        )
        self.assertTrue(item.product_snapshot_captured)
        self.assertEqual(item.product_reference, 'VN12')
        self.assertEqual(item.display_color, 'Azul')
        self.assertEqual(item.display_size, 'M')

        self.category.prefix = 'AT'
        self.category.save(update_fields=['prefix', 'updated_at'])
        self.product.code = 99
        self.product.description = 'Cadastro alterado'
        self.product.color = 'Vermelho'
        self.product.size = 'G'
        self.product.save()

        contract = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        detail = self.client.get(reverse('rentals:detail', args=[self.rental.pk]))
        edit = self.client.get(reverse('rentals:update', args=[self.rental.pk]))

        for response in (contract, detail):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'VN12')
            self.assertContains(response, 'Vestido clássico')
            self.assertContains(response, 'Azul')
            self.assertContains(response, 'M')
            self.assertNotContains(response, 'Cadastro alterado')
            self.assertNotContains(response, 'Vermelho')

        self.assertEqual(edit.status_code, 200)
        self.assertContains(edit, 'VN12')
        self.assertContains(edit, 'Vestido clássico')
        self.assertContains(edit, 'Azul')
        self.assertContains(edit, 'M')
        self.assertContains(contract, 'Vestido clássico — Azul — M')

    def test_deliberate_product_swap_refreshes_snapshot_with_update_fields(self):
        item = RentalItem.objects.create(
            rental=self.rental,
            product=self.product,
            value=self.product.value,
        )
        other_category = Category.objects.create(prefix='AC', name='Acessórios')
        other = Product.objects.create(
            category=other_category,
            code=7,
            description='Tiara',
            color='Prata',
            size='Único',
            value=Decimal('80'),
        )

        item.product = other
        item.save(update_fields=['product'])
        item.refresh_from_db()

        self.assertEqual(item.product_reference, 'AC7')
        self.assertEqual(item.display_description, 'Tiara')
        self.assertEqual(item.display_color, 'Prata')
        self.assertEqual(item.display_size, 'Único')

        other.description = 'Tiara alterada'
        other.save()
        item.value = Decimal('75')
        item.save(update_fields=['value'])
        item.refresh_from_db()
        self.assertEqual(item.display_description, 'Tiara')

    def test_objects_create_with_product_id_captures_snapshot(self):
        item = RentalItem.objects.create(
            rental=self.rental,
            product_id=self.product.pk,
            value=self.product.value,
        )

        self.assertTrue(item.product_snapshot_captured)
        self.assertEqual(item.product_reference, 'VN12')
        self.assertEqual(item.display_description, 'Vestido clássico')

    def test_inactive_product_is_rejected_for_new_item_but_kept_on_existing_item(self):
        self.product.is_active = False
        self.product.save(update_fields=['is_active', 'updated_at'])
        payload = {
            'items-0-product': self.product.pk,
            'items-0-description': '',
            'items-0-value': '300,00',
        }

        new_form = RentalItemForm(data=payload, prefix='items-0')
        self.assertFalse(new_form.is_valid())
        self.assertIn('product', new_form.errors)

        item = RentalItem.objects.create(
            rental=self.rental,
            product=self.product,
            value=self.product.value,
        )
        existing_form = RentalItemForm(data=payload, prefix='items-0', instance=item)
        self.assertTrue(existing_form.is_valid(), existing_form.errors)


class RentalListPaginationTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email='rental-pagination@test.com',
            password='pass',
        )
        ModulePermission.objects.create(
            user=user,
            module_key='rentals',
            allowed=True,
        )
        self.client.force_login(user)
        customer = Customer.objects.create(name='Cliente Paginação')
        for number in range(1, 22):
            Rental.objects.create(
                number=number,
                customer=customer,
                pickup_date=date(2026, 7, 1),
                return_date=date(2026, 7, 2),
                status=Rental.Status.PENDING,
            )

    def test_pagination_preserves_active_filters(self):
        response = self.client.get(
            reverse('rentals:list'),
            {'q': 'Cliente', 'status': Rental.Status.PENDING},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '?q=Cliente&amp;status=pending&amp;page=2',
        )


class ClientCorrectionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='correcoes-cliente@noivasecia.test',
            password='Senha12345',
        )
        ModulePermission.objects.create(
            user=self.user,
            module_key='rentals',
            allowed=True,
        )
        self.client.force_login(self.user)
        self.company = Company.load()
        self.customer = Customer.objects.create(name='Cliente Teste Correções')
        self.rental = Rental.objects.create(
            number=9999,
            customer=self.customer,
            pickup_date=date(2026, 8, 10),
            return_date=date(2026, 8, 15),
            status=Rental.Status.PENDING,
            total_value=500,
        )

    def test_printed_contract_contains_updated_clauses_and_single_witness(self):
        url = reverse('rentals:contract', args=[self.rental.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Cláusula 1 com data de devolução
        self.assertIn('data de devolução estipulada acima (15/08/2026)', content)
        # Cláusula 3 com espaço para quantia de reposição
        self.assertIn('na quantia de R$ (____________________)', content)
        # Cláusula 5 com multa de 50% por desistência/rescisão — sem centavos
        self.assertIn('será cobrado o percentual de 50% correspondente ao valor total da locação', content)
        # Cláusula 6 com perda em 7 dias
        self.assertIn('6. A não devolução dos trajes', content)
        self.assertIn('implica cobrança de 100% correspondente', content)
        # Sem multa cadastrada: bloco dos Dados da Locação omitido e cláusula 4
        # não promete um valor que o contrato não mostra.
        self.assertNotIn('<span class="field-caption">Multa</span>', content)
        self.assertIn(
            '4. O atraso na devolução sujeita o locatário à multa de 10% do valor '
            'total da locação por dia de atraso, limitada a 7 dias, além de '
            'juros moratórios e demais penalidades aplicáveis.',
            content,
        )
        # Assinatura com 1 testemunha
        self.assertIn('Testemunha', content)
        self.assertNotIn('Testemunha 2', content)

    def test_printed_contract_states_penalty_is_charged_once(self):
        self.rental.penalty_value = Decimal('960.00')
        self.rental.save(update_fields=['penalty_value'])

        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        content = response.content.decode('utf-8')

        self.assertIn('<span class="field-caption">Valor de reposição</span>', content)
        self.assertIn(
            '4. O atraso na devolução sujeita o locatário à multa de 10% do valor '
            'total da locação por dia de atraso, limitada a 7 dias, além de '
            'juros moratórios e demais penalidades aplicáveis.',
            content,
        )
        self.assertNotIn('diária', content)

    def test_printed_contract_percentages_follow_configured_rates(self):
        self.company.cancellation_penalty_rate = Decimal('0')
        self.company.loss_penalty_rate = Decimal('12.50')
        self.company.save(update_fields=[
            'cancellation_penalty_rate', 'loss_penalty_rate',
        ])

        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        content = response.content.decode('utf-8')

        # A 0% rate must print as 0%, not fall back to the old hardcoded 50%.
        self.assertIn('será cobrado o percentual de 0% correspondente', content)
        self.assertIn('implica cobrança de 12,5% correspondente', content)

    def test_printed_contract_shows_damage_amount_when_return_recorded(self):
        Pickup.objects.create(rental=self.rental, pickup_date=date(2026, 8, 10))
        Return.objects.create(
            rental=self.rental,
            return_date=date(2026, 8, 15),
            damage_amount=Decimal('80.00'),
        )

        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        content = response.content.decode('utf-8')

        self.assertIn('a quantia de R$ 80,00.', content)
        self.assertNotIn('a quantia de (____________________)', content)

    def test_printed_contract_shows_blank_when_no_damage_recorded(self):
        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        content = response.content.decode('utf-8')

        self.assertIn('na quantia de R$ (____________________)', content)

    def test_printed_contract_shows_wearer_name_when_set(self):
        self.rental.wearer_name = 'Julio Cesar'
        self.rental.save(update_fields=['wearer_name'])

        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        content = response.content.decode('utf-8')

        self.assertIn('Quem vai usar', content)
        self.assertIn('Julio Cesar', content)

    def test_printed_contract_hides_wearer_field_when_blank(self):
        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        content = response.content.decode('utf-8')

        self.assertNotIn('Quem vai usar', content)

    def _add_item(self, wearer_name=''):
        category, _ = Category.objects.get_or_create(prefix='TER', defaults={'name': 'Ternos'})
        product = Product.objects.create(
            category=category, code=self.rental.items.count() + 1,
            description='Terno', color='Preto', size='48', value=Decimal('300.00'),
        )
        return RentalItem.objects.create(
            rental=self.rental, product=product, value=Decimal('300.00'),
            wearer_name=wearer_name,
        )

    def test_printed_contract_shows_per_item_wearer_when_items_have_it(self):
        self._add_item(wearer_name='José Francisco')
        self._add_item(wearer_name='Fernanda')

        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        content = response.content.decode('utf-8')

        self.assertIn('<th style="width:18%">Quem vai usar</th>', content)
        self.assertIn('José Francisco', content)
        self.assertIn('Fernanda', content)

    def test_printed_contract_omits_wearer_column_when_no_item_has_it(self):
        self._add_item()

        response = self.client.get(reverse('rentals:contract', args=[self.rental.pk]))
        content = response.content.decode('utf-8')

        self.assertNotIn('<th style="width:18%">Quem vai usar</th>', content)

    def test_rental_item_keeps_its_wearer_when_saved_from_the_grid(self):
        """The grid no longer edits this field, so a save must not blank it."""
        item = self._add_item(wearer_name='José Francisco')

        form = RentalItemForm(
            data={'product': item.product_id, 'description': '', 'value': '300,00'},
            instance=item,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()

        self.assertEqual(saved.wearer_name, 'José Francisco')

    def test_payment_plan_generation_supports_last_due_date(self):
        from billing.services import generate_for_rental
        receivables = generate_for_rental(
            self.rental,
            installments=3,
            last_due_date=self.rental.pickup_date,
        )
        self.assertEqual(len(receivables), 3)
        self.assertEqual(receivables[2].due_date, date(2026, 8, 10))
        self.assertEqual(receivables[1].due_date, date(2026, 7, 10))
        self.assertEqual(receivables[0].due_date, date(2026, 6, 10))

    def test_payment_plan_last_due_date_with_single_installment(self):
        from billing.services import generate_for_rental
        receivables = generate_for_rental(
            self.rental,
            installments=1,
            last_due_date=self.rental.pickup_date,
        )
        self.assertEqual(len(receivables), 1)
        self.assertEqual(receivables[0].due_date, date(2026, 8, 10))

    def test_create_rental_payment_plan_defaults_last_installment_to_pickup_date(self):
        from billing.services import create_rental_payment_plan
        result = create_rental_payment_plan(self.rental, installments=3)
        future = result['future_receivables']
        self.assertEqual(len(future), 3)
        self.assertEqual(future[-1].due_date, self.rental.pickup_date)



class RentalContractCompanyCustomerTests(TestCase):
    """A company rental prints the CNPJ where an individual shows CPF/RG."""

    def setUp(self):
        user = User.objects.create_user(
            email='contrato-cnpj@noivasecia.test',
            password='Senha12345',
        )
        ModulePermission.objects.create(user=user, module_key='rentals', allowed=True)
        self.client.force_login(user)
        Company.load()

    def _contract_for(self, customer, number):
        rental = Rental.objects.create(
            number=number,
            customer=customer,
            pickup_date=date(2026, 9, 10),
            return_date=date(2026, 9, 15),
            total_value=Decimal('500.00'),
        )
        response = self.client.get(reverse('rentals:contract', args=[rental.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_company_customer_prints_cnpj_instead_of_cpf_and_rg(self):
        company_customer = Customer.objects.create(
            name='Construtora Alfa Ltda',
            cnpj='11.222.333/0001-81',
        )

        content = self._contract_for(company_customer, number=8801)

        self.assertIn('<span class="field-caption">CNPJ</span>', content)
        self.assertIn('11.222.333/0001-81', content)
        self.assertNotIn('<span class="field-caption">CPF</span>', content)
        self.assertNotIn('<span class="field-caption">RG</span>', content)

    def test_individual_customer_still_prints_cpf_and_rg(self):
        individual = Customer.objects.create(
            name='Maria Silva',
            cpf='529.982.247-25',
            rg='8.241.995-0',
        )

        content = self._contract_for(individual, number=8802)

        self.assertIn('<span class="field-caption">CPF</span>', content)
        self.assertIn('<span class="field-caption">RG</span>', content)
        self.assertNotIn('<span class="field-caption">CNPJ</span>', content)


class ContractCapacityLimitsTests(TestCase):
    """The printed contract holds 15 pieces and one entry plus 8 installments.

    Measured on the rendered contract: two copies share one A4 sheet of 285mm,
    and the pair takes 281.2mm with 15 items but exactly 285.0mm with 16 — no
    margin left. 15 is also the ceiling in the imported legacy data.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='cap@test.com', password='x', is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.user)
        Company.objects.create(name='Noivas & Cia', last_rental_number=0)
        self.customer = Customer.objects.create(name='Cliente Teste')
        cat = Category.objects.create(prefix='TR', name='Trajes')
        self.products = [
            Product.objects.create(category=cat, code=i, description=f'Peça {i}', value=10)
            for i in range(1, 20)
        ]

    def _payload(self, product_list):
        data = {
            'customer': self.customer.pk,
            'pickup_date': '2026-06-10',
            'return_date': '2026-06-15',
            'penalty_value': '0',
            'notes': '',
            'items-TOTAL_FORMS': str(len(product_list)),
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
        }
        for i, product in enumerate(product_list):
            data[f'items-{i}-product'] = product.pk
            data[f'items-{i}-description'] = product.description
            data[f'items-{i}-value'] = '10'
            data[f'items-{i}-DELETE'] = ''
        return data

    def test_new_rental_accepts_fifteen_items(self):
        response = self.client.post('/locacoes/nova/', self._payload(self.products[:15]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Rental.objects.get().items.count(), 15)

    def test_new_rental_rejects_sixteen_items(self):
        response = self.client.post('/locacoes/nova/', self._payload(self.products[:16]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Rental.objects.exists())
        self.assertContains(response, 'no máximo 15 peças')

    def test_rental_over_the_limit_still_saves_without_new_items(self):
        rental = Rental.objects.create(
            customer=self.customer, number=901,
            pickup_date=date(2026, 6, 10), return_date=date(2026, 6, 15),
            penalty_value=Decimal('0'),
        )
        for product in self.products[:16]:
            RentalItem.objects.create(
                rental=rental, product=product,
                description=product.description, value=Decimal('10'),
            )

        data = self._payload(self.products[:16])
        data['items-INITIAL_FORMS'] = '16'
        for i, item in enumerate(rental.items.order_by('pk')):
            data[f'items-{i}-id'] = item.pk
        data['items-TOTAL_FORMS'] = '16'

        response = self.client.post(f'/locacoes/{rental.pk}/editar/', data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(rental.items.count(), 16)


class InstallmentCountLimitTests(TestCase):
    """Entry is mandatory and there are at most 8 future installments."""

    def setUp(self):
        Company.objects.create(name='Noivas & Cia', last_rental_number=0)
        self.customer = Customer.objects.create(name='Cliente Teste')

    def _form(self, count):
        return RentalForm(data={
            'customer': self.customer.pk,
            'pickup_date': '10/06/2026',
            'return_date': '15/06/2026',
            'penalty_value': '0,00',
            'notes': '',
            'installment_count': str(count),
        })

    def test_eight_installments_is_accepted(self):
        self.assertNotIn('installment_count', self._form(8).errors)

    def test_nine_installments_is_rejected(self):
        self.assertIn('installment_count', self._form(9).errors)
