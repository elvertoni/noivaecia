"""Tests for the WhatsApp review-and-dispatch panel (UI layer).

Backend primitives (`pickup_reminder_queue`, `return_reminder_queue`,
`dispatch_customer_message`) are already covered in `tests_services.py`; here
we only exercise the view/template wiring — gating, queue rendering, and the
dispatch POST endpoint.
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import ModulePermission
from company.models import Company
from customers.models import Customer
from notifications import views as panel_views
from notifications.models import CustomerMessage
from rentals.models import Rental

User = get_user_model()
TODAY = date(2026, 7, 20)
TOMORROW = TODAY + timedelta(days=1)


_user_seq = 0


def _make_user(module_key='movements'):
    global _user_seq
    _user_seq += 1
    user = User.objects.create_user(email=f'panel{_user_seq}@test.com', password='pass')
    if module_key:
        ModulePermission.objects.create(user=user, module_key=module_key, allowed=True)
    return user


def _make_company():
    Company.objects.filter(pk=1).delete()
    return Company.objects.create(name='Noivas Cia', last_rental_number=1)


def _make_customer(name='Maria Silva', mobile='11987654321'):
    return Customer.objects.create(name=name, city='Bandeirantes', phone_mobile=mobile)


def _make_pickup_rental(number=900, customer=None):
    customer = customer or _make_customer(f'Cliente Retirada {number}')
    return Rental.objects.create(
        number=number, customer=customer, status=Rental.Status.PENDING,
        pickup_date=TOMORROW, return_date=TOMORROW + timedelta(days=7),
        total_value=Decimal('300'),
    )


def _make_return_rental(number=901, customer=None):
    customer = customer or _make_customer(f'Cliente Devolução {number}')
    return Rental.objects.create(
        number=number, customer=customer, status=Rental.Status.PICKED_UP,
        pickup_date=TODAY - timedelta(days=7), return_date=TODAY,
        total_value=Decimal('300'),
    )


class PanelAccessTests(TestCase):
    def setUp(self):
        _make_company()
        self.url = reverse('notifications:whatsapp_panel')

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r.url)

    def test_user_without_movements_module_gets_403(self):
        user = _make_user(module_key='catalog')
        self.client.force_login(user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 403)

    def test_user_with_movements_module_gets_200(self):
        user = _make_user()
        self.client.force_login(user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)


class PanelQueueRenderingTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('notifications:whatsapp_panel')

    @mock.patch('notifications.views.timezone')
    def test_pickup_and_return_queues_appear(self, mock_timezone):
        mock_timezone.localdate.return_value = TODAY
        pickup_rental = _make_pickup_rental()
        return_rental = _make_return_rental()

        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Retiradas de amanhã')
        self.assertContains(r, 'Devoluções de hoje')
        pickup_pks = {item['rental'].pk for item in r.context['pickup_items']}
        return_pks = {item['rental'].pk for item in r.context['return_items']}
        self.assertIn(pickup_rental.pk, pickup_pks)
        self.assertIn(return_rental.pk, return_pks)

    @mock.patch('notifications.views.timezone')
    def test_empty_queues_show_friendly_state(self, mock_timezone):
        mock_timezone.localdate.return_value = TODAY
        r = self.client.get(self.url)
        self.assertContains(r, 'Nenhuma retirada para avisar amanhã.')
        self.assertContains(r, 'Nenhuma devolução para avisar hoje.')

    @mock.patch('notifications.views.timezone')
    def test_already_sent_rental_does_not_reappear_in_queue(self, mock_timezone):
        mock_timezone.localdate.return_value = TODAY
        rental = _make_pickup_rental()
        CustomerMessage.objects.create(
            rental=rental, customer=rental.customer,
            kind=CustomerMessage.Kind.PICKUP_REMINDER, phone='5511987654321',
            status=CustomerMessage.Status.SENT,
        )
        r = self.client.get(self.url)
        pickup_pks = {item['rental'].pk for item in r.context['pickup_items']}
        self.assertNotIn(rental.pk, pickup_pks)

    def test_recent_messages_section_lists_sent_records(self):
        rental = _make_return_rental()
        CustomerMessage.objects.create(
            rental=rental, customer=rental.customer,
            kind=CustomerMessage.Kind.RETURN_REMINDER, phone='5511987654321',
            status=CustomerMessage.Status.SENT,
        )
        r = self.client.get(self.url)
        self.assertContains(r, 'Enviados recentemente')
        self.assertContains(r, rental.customer.name)


@override_settings()
class DispatchViewTests(TestCase):
    def setUp(self):
        _make_company()
        self.user = _make_user()
        self.client.force_login(self.user)
        self.url = reverse('notifications:dispatch')
        self.panel_url = reverse('notifications:whatsapp_panel')
        # Avoid slowing the suite down with the anti-ban throttle.
        self._spacing_patch = mock.patch.object(panel_views, 'SEND_SPACING_SECONDS', 0)
        self._spacing_patch.start()
        self.addCleanup(self._spacing_patch.stop)

    @mock.patch('notifications.services.evolution.send_text', return_value='MSGID1')
    def test_dispatch_selected_rentals_sends_and_records(self, send_text):
        rental = _make_pickup_rental()
        r = self.client.post(self.url, {
            'kind': CustomerMessage.Kind.PICKUP_REMINDER,
            'rental_ids': [rental.pk],
        }, follow=True)
        self.assertRedirects(r, self.panel_url)
        send_text.assert_called_once()
        msg = CustomerMessage.objects.get(rental=rental, kind=CustomerMessage.Kind.PICKUP_REMINDER)
        self.assertEqual(msg.status, CustomerMessage.Status.SENT)
        messages_shown = [str(m) for m in r.context['messages']]
        self.assertTrue(any('1 aviso(s) enviado(s), 0 falha(s).' in m for m in messages_shown))

    @mock.patch('notifications.services.timezone.localdate', return_value=TODAY)
    @mock.patch('notifications.services.evolution.send_text', return_value='MSGID2')
    def test_dispatch_send_all_covers_entire_queue(self, send_text, mock_localdate):
        rental_a = _make_pickup_rental(910)
        rental_b = _make_pickup_rental(911)
        r = self.client.post(self.url, {
            'kind': CustomerMessage.Kind.PICKUP_REMINDER,
            'send_all': '1',
        }, follow=True)
        self.assertEqual(send_text.call_count, 2)
        self.assertTrue(
            CustomerMessage.objects.filter(rental=rental_a, status=CustomerMessage.Status.SENT).exists()
        )
        self.assertTrue(
            CustomerMessage.objects.filter(rental=rental_b, status=CustomerMessage.Status.SENT).exists()
        )

    @mock.patch('notifications.services.evolution.send_text')
    def test_invalid_kind_shows_error_and_sends_nothing(self, send_text):
        rental = _make_pickup_rental()
        r = self.client.post(self.url, {
            'kind': 'not_a_real_kind',
            'rental_ids': [rental.pk],
        }, follow=True)
        send_text.assert_not_called()
        self.assertFalse(CustomerMessage.objects.filter(rental=rental).exists())
        messages_shown = [str(m) for m in r.context['messages']]
        self.assertTrue(any('inválido' in m for m in messages_shown))

    @mock.patch('notifications.services.timezone.localdate', return_value=TODAY)
    @mock.patch('notifications.services.evolution.send_text')
    def test_dispatch_rejects_rentals_not_in_the_visible_queue(self, send_text, mock_localdate):
        rental = _make_return_rental()

        r = self.client.post(self.url, {
            'kind': CustomerMessage.Kind.PICKUP_REMINDER,
            'rental_ids': [rental.pk],
        }, follow=True)

        send_text.assert_not_called()
        self.assertFalse(CustomerMessage.objects.filter(rental=rental).exists())
        messages_shown = [str(m) for m in r.context['messages']]
        self.assertTrue(any('não estão disponíveis' in m for m in messages_shown))

    def test_gating_blocks_dispatch_for_user_without_module(self):
        outsider = _make_user(module_key='catalog')
        self.client.force_login(outsider)
        rental = _make_pickup_rental()
        r = self.client.post(self.url, {
            'kind': CustomerMessage.Kind.PICKUP_REMINDER,
            'rental_ids': [rental.pk],
        })
        self.assertEqual(r.status_code, 403)
