"""Tests for the customer-facing WhatsApp backend primitives: phone
formatting, message rendering, reminder queues and dispatch. No test touches
the network — ``notifications.services.evolution.send_text`` is always
mocked."""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from catalog.models import Category, Product
from company.models import Company
from customers.models import Customer
from notifications import evolution
from notifications.models import CustomerMessage
from notifications.services import (
    MessageTemplateError,
    dispatch_customer_message,
    format_whatsapp_number,
    pickup_reminder_queue,
    render_pickup_message,
    render_return_message,
    return_reminder_queue,
    validate_message_template,
)
from rentals.models import Rental, RentalItem

TODAY = date(2026, 7, 20)


def _make_company():
    Company.objects.filter(pk=1).delete()
    return Company.objects.create(name='Noivas Cia', last_rental_number=1)


def _make_customer(name='Maria Silva', phone_mobile='(43) 99999-8888'):
    return Customer.objects.create(name=name, city='Bandeirantes', phone_mobile=phone_mobile)


def _make_product(code):
    category = Category.objects.get_or_create(prefix='VF', defaults={'name': 'Vestido de festa'})[0]
    return Product.objects.create(category=category, code=code, description='Vestido', value=Decimal('300'))


def _make_rental(number, customer, status, pickup_date, return_date, item_count=1):
    rental = Rental.objects.create(
        number=number, customer=customer, status=status,
        pickup_date=pickup_date, return_date=return_date,
        total_value=Decimal('300'),
    )
    for i in range(item_count):
        product = _make_product(f'{number}{i}')
        RentalItem.objects.create(rental=rental, product=product, value=Decimal('300'))
    return rental


# ── format_whatsapp_number ──────────────────────────────────────────────────

class FormatWhatsappNumberTests(TestCase):
    def test_eleven_digits_gets_55_prefix(self):
        self.assertEqual(format_whatsapp_number('43999998888'), '5543999998888')

    def test_ten_digits_gets_55_prefix(self):
        self.assertEqual(format_whatsapp_number('4333221100'), '554333221100')

    def test_already_prefixed_with_55_and_13_digits_is_used_as_is(self):
        self.assertEqual(format_whatsapp_number('5543999998888'), '5543999998888')

    def test_already_prefixed_with_55_and_12_digits_is_used_as_is(self):
        self.assertEqual(format_whatsapp_number('554333221100'), '554333221100')

    def test_strips_mask_characters(self):
        self.assertEqual(format_whatsapp_number('(43) 99999-8888'), '5543999998888')

    def test_too_short_is_invalid(self):
        self.assertIsNone(format_whatsapp_number('99998888'))

    def test_too_long_is_invalid(self):
        self.assertIsNone(format_whatsapp_number('55439999988889999'))

    def test_empty_is_invalid(self):
        self.assertIsNone(format_whatsapp_number(''))
        self.assertIsNone(format_whatsapp_number(None))


# ── render_pickup_message / render_return_message ──────────────────────────

class RenderMessagesTests(TestCase):
    def setUp(self):
        _make_company()

    def test_pickup_message_singular_item(self):
        customer = _make_customer(name='MARIA DA SILVA')
        rental = _make_rental(1, customer, Rental.Status.PENDING,
                               pickup_date=date(2026, 7, 21), return_date=date(2026, 7, 28),
                               item_count=1)
        text = render_pickup_message(rental)
        self.assertIn('Oi, Maria!', text)
        self.assertIn('21/07', text)
        self.assertIn('sua peça', text)
        self.assertNotIn('suas peças', text)
        self.assertTrue(text.startswith(
            'Oi, Maria! 💛 Aqui é a Ana, da Noivas & Cia. Passando pra avisar '
            'com carinho que a partir de amanhã, 21/07, você já pode retirar '
            'sua peça aqui na loja'
        ))
        self.assertTrue(text.endswith('Um abraço carinhoso 🌸'))

    def test_pickup_message_plural_items(self):
        customer = _make_customer(name='Ana Costa')
        rental = _make_rental(2, customer, Rental.Status.PENDING,
                               pickup_date=date(2026, 7, 21), return_date=date(2026, 7, 28),
                               item_count=2)
        text = render_pickup_message(rental)
        self.assertIn('suas peças', text)
        self.assertNotIn('retirar sua peça', text)

    def test_return_message_singular_item(self):
        customer = _make_customer(name='carla souza')
        rental = _make_rental(3, customer, Rental.Status.PICKED_UP,
                               pickup_date=date(2026, 7, 10), return_date=TODAY,
                               item_count=1)
        text = render_return_message(rental)
        self.assertIn('Oi, Carla!', text)
        self.assertIn('de sua peça', text)
        self.assertNotIn('de suas peças', text)
        self.assertTrue(text.startswith(
            'Oi, Carla! 💛 Aqui é a Ana, da Noivas & Cia. Espero de coração '
            'que seu evento tenha sido lindo! 🥂'
        ))
        self.assertTrue(text.endswith('Um beijo! 🌷'))

    def test_return_message_plural_items(self):
        customer = _make_customer(name='Bruno Lima')
        rental = _make_rental(4, customer, Rental.Status.PICKED_UP,
                               pickup_date=date(2026, 7, 10), return_date=TODAY,
                               item_count=2)
        text = render_return_message(rental)
        self.assertIn('de suas peças', text)

    def test_custom_template_substitutes_each_supported_placeholder(self):
        customer = _make_customer(name='MARIA DA SILVA')
        rental = _make_rental(
            12,
            customer,
            Rental.Status.PENDING,
            pickup_date=date(2026, 7, 21),
            return_date=date(2026, 7, 28),
            item_count=2,
        )
        text = render_pickup_message(
            rental,
            'Oi, {cliente}! Locação {numero_locacao}: {itens}. Retirada {data_retirada}; devolução {data_devolucao}.',
        )
        self.assertEqual(
            text,
            'Oi, Maria! Locação 12: suas peças. Retirada 21/07; devolução 28/07.',
        )

    def test_unknown_template_placeholder_is_rejected(self):
        with self.assertRaisesMessage(MessageTemplateError, 'Placeholder inválido'):
            validate_message_template('Oi, {nome}!')


# ── pickup_reminder_queue ────────────────────────────────────────────────────

class PickupReminderQueueTests(TestCase):
    def setUp(self):
        _make_company()

    def test_pending_rental_with_pickup_tomorrow_is_included(self):
        customer = _make_customer(name='Zilda Torres')
        rental = _make_rental(10, customer, Rental.Status.PENDING,
                               pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        queue = pickup_reminder_queue(TODAY)
        self.assertEqual(len(queue), 1)
        entry = queue[0]
        self.assertEqual(entry['rental'], rental)
        self.assertEqual(entry['customer'], customer)
        self.assertEqual(entry['phone'], '5543999998888')
        self.assertIn('Zilda', entry['message'])

    def test_wrong_pickup_date_is_excluded(self):
        customer = _make_customer(name='Data Errada')
        _make_rental(11, customer, Rental.Status.PENDING,
                     pickup_date=TODAY + timedelta(days=2), return_date=TODAY + timedelta(days=7))
        _make_rental(12, customer, Rental.Status.PENDING,
                     pickup_date=TODAY, return_date=TODAY + timedelta(days=7))
        queue = pickup_reminder_queue(TODAY)
        self.assertEqual(queue, [])

    def test_wrong_status_is_excluded(self):
        customer = _make_customer(name='Status Errado')
        _make_rental(13, customer, Rental.Status.PICKED_UP,
                     pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        queue = pickup_reminder_queue(TODAY)
        self.assertEqual(queue, [])

    def test_invalid_phone_is_excluded(self):
        customer = _make_customer(name='Sem Telefone', phone_mobile='')
        _make_rental(14, customer, Rental.Status.PENDING,
                     pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        queue = pickup_reminder_queue(TODAY)
        self.assertEqual(queue, [])

    def test_already_sent_rental_is_excluded(self):
        customer = _make_customer(name='Ja Avisada')
        rental = _make_rental(15, customer, Rental.Status.PENDING,
                               pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        CustomerMessage.objects.create(
            rental=rental, customer=customer, kind=CustomerMessage.Kind.PICKUP_REMINDER,
            phone='5543999998888', status=CustomerMessage.Status.SENT,
        )
        queue = pickup_reminder_queue(TODAY)
        self.assertEqual(queue, [])

    def test_failed_previous_attempt_remains_eligible(self):
        customer = _make_customer(name='Falha Antes')
        rental = _make_rental(16, customer, Rental.Status.PENDING,
                               pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        CustomerMessage.objects.create(
            rental=rental, customer=customer, kind=CustomerMessage.Kind.PICKUP_REMINDER,
            phone='5543999998888', status=CustomerMessage.Status.FAILED, error='boom',
        )
        queue = pickup_reminder_queue(TODAY)
        self.assertEqual(len(queue), 1)

    def test_queue_is_ordered_by_customer_name(self):
        z = _make_customer(name='Zilda')
        a = _make_customer(name='Alice')
        _make_rental(17, z, Rental.Status.PENDING,
                     pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        _make_rental(18, a, Rental.Status.PENDING,
                     pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        queue = pickup_reminder_queue(TODAY)
        names = [entry['customer'].name for entry in queue]
        self.assertEqual(names, ['Alice', 'Zilda'])

    def test_defaults_to_localdate_when_today_omitted(self):
        customer = _make_customer(name='Sem Today')
        from django.utils import timezone as dj_timezone
        tomorrow = dj_timezone.localdate() + timedelta(days=1)
        _make_rental(19, customer, Rental.Status.PENDING,
                     pickup_date=tomorrow, return_date=tomorrow + timedelta(days=7))
        queue = pickup_reminder_queue()
        self.assertEqual(len(queue), 1)

    def test_sent_message_with_deleted_rental_does_not_empty_the_queue(self):
        """A SENT CustomerMessage whose rental was later deleted (rental_id
        goes NULL via on_delete=SET_NULL) must not blank out every other
        rental's eligibility: NULL inside a NOT IN (...) makes the whole
        clause match nothing in SQL, which previously zeroed the queue."""
        orphan_customer = _make_customer(name='Cliente Removida')
        orphan_rental = _make_rental(20, orphan_customer, Rental.Status.PENDING,
                                      pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        CustomerMessage.objects.create(
            rental=orphan_rental, customer=orphan_customer, kind=CustomerMessage.Kind.PICKUP_REMINDER,
            phone='5543999998888', status=CustomerMessage.Status.SENT,
        )
        orphan_rental.delete()

        customer = _make_customer(name='Zilda Torres')
        rental = _make_rental(21, customer, Rental.Status.PENDING,
                               pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7))
        queue = pickup_reminder_queue(TODAY)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['rental'], rental)


# ── return_reminder_queue ────────────────────────────────────────────────────

class ReturnReminderQueueTests(TestCase):
    def setUp(self):
        _make_company()

    def test_picked_up_rental_with_return_today_is_included(self):
        customer = _make_customer(name='Carla Souza')
        rental = _make_rental(30, customer, Rental.Status.PICKED_UP,
                               pickup_date=TODAY - timedelta(days=7), return_date=TODAY)
        queue = return_reminder_queue(TODAY)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['rental'], rental)
        self.assertIn('Carla', queue[0]['message'])

    def test_wrong_return_date_is_excluded(self):
        customer = _make_customer(name='Data Errada Dev')
        _make_rental(31, customer, Rental.Status.PICKED_UP,
                     pickup_date=TODAY - timedelta(days=7), return_date=TODAY + timedelta(days=1))
        queue = return_reminder_queue(TODAY)
        self.assertEqual(queue, [])

    def test_wrong_status_is_excluded(self):
        customer = _make_customer(name='Pendente Dev')
        _make_rental(32, customer, Rental.Status.PENDING,
                     pickup_date=TODAY, return_date=TODAY)
        queue = return_reminder_queue(TODAY)
        self.assertEqual(queue, [])

    def test_returned_status_is_excluded(self):
        customer = _make_customer(name='Ja Devolvido')
        _make_rental(33, customer, Rental.Status.RETURNED,
                     pickup_date=TODAY - timedelta(days=7), return_date=TODAY)
        queue = return_reminder_queue(TODAY)
        self.assertEqual(queue, [])

    def test_invalid_phone_is_excluded(self):
        customer = _make_customer(name='Sem Tel Dev', phone_mobile='123')
        _make_rental(34, customer, Rental.Status.PICKED_UP,
                     pickup_date=TODAY - timedelta(days=7), return_date=TODAY)
        queue = return_reminder_queue(TODAY)
        self.assertEqual(queue, [])

    def test_already_sent_rental_is_excluded(self):
        customer = _make_customer(name='Ja Cobrada')
        rental = _make_rental(35, customer, Rental.Status.PICKED_UP,
                               pickup_date=TODAY - timedelta(days=7), return_date=TODAY)
        CustomerMessage.objects.create(
            rental=rental, customer=customer, kind=CustomerMessage.Kind.RETURN_REMINDER,
            phone='5543999998888', status=CustomerMessage.Status.SENT,
        )
        queue = return_reminder_queue(TODAY)
        self.assertEqual(queue, [])


# ── dispatch_customer_message ───────────────────────────────────────────────

CMD = 'notifications.services'


class DispatchCustomerMessageTests(TestCase):
    def setUp(self):
        _make_company()
        self.customer = _make_customer(name='Debora Rocha')
        self.rental = _make_rental(
            40, self.customer, Rental.Status.PENDING,
            pickup_date=TODAY + timedelta(days=1), return_date=TODAY + timedelta(days=7),
        )

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID1')
    def test_successful_send_creates_sent_record(self, send_text):
        record = dispatch_customer_message(
            self.rental, CustomerMessage.Kind.PICKUP_REMINDER,
        )
        send_text.assert_called_once_with('5543999998888', mock.ANY)
        self.assertEqual(record.status, CustomerMessage.Status.SENT)
        self.assertEqual(record.message_id, 'MSGID1')
        self.assertEqual(record.phone, '5543999998888')
        self.assertIsNotNone(record.sent_at)
        self.assertEqual(record.rental, self.rental)
        self.assertEqual(record.customer, self.customer)

    @mock.patch(f'{CMD}.evolution.send_text', side_effect=evolution.EvolutionError('falha na api'))
    def test_evolution_error_creates_failed_record(self, send_text):
        record = dispatch_customer_message(
            self.rental, CustomerMessage.Kind.PICKUP_REMINDER,
        )
        self.assertEqual(record.status, CustomerMessage.Status.FAILED)
        self.assertEqual(record.error, 'falha na api')
        self.assertIsNone(record.sent_at)

    @mock.patch(f'{CMD}.evolution.send_text')
    def test_invalid_phone_creates_failed_record_without_api_call(self, send_text):
        self.customer.phone_mobile = ''
        self.customer.save()
        record = dispatch_customer_message(
            self.rental, CustomerMessage.Kind.PICKUP_REMINDER,
        )
        send_text.assert_not_called()
        self.assertEqual(record.status, CustomerMessage.Status.FAILED)
        self.assertEqual(record.error, 'telefone inválido')

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID2')
    def test_second_call_is_idempotent_and_does_not_resend(self, send_text):
        first = dispatch_customer_message(self.rental, CustomerMessage.Kind.PICKUP_REMINDER)
        second = dispatch_customer_message(self.rental, CustomerMessage.Kind.PICKUP_REMINDER)
        send_text.assert_called_once()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            CustomerMessage.objects.filter(rental=self.rental).count(), 1
        )

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID3')
    def test_return_kind_uses_return_message(self, send_text):
        rental = _make_rental(41, self.customer, Rental.Status.PICKED_UP,
                               pickup_date=TODAY - timedelta(days=7), return_date=TODAY)
        record = dispatch_customer_message(rental, CustomerMessage.Kind.RETURN_REMINDER)
        sent_text = send_text.call_args.args[1]
        self.assertIn('devolução', sent_text)
        self.assertEqual(record.kind, CustomerMessage.Kind.RETURN_REMINDER)

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID4')
    def test_sent_by_user_is_recorded(self, send_text):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(email='ana@test.com', password='pass')
        record = dispatch_customer_message(
            self.rental, CustomerMessage.Kind.PICKUP_REMINDER, user=user,
        )
        self.assertEqual(record.sent_by, user)

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID5')
    def test_failed_then_retried_creates_second_sent_record(self, send_text):
        send_text.side_effect = [evolution.EvolutionError('boom'), 'MSGID5']
        first = dispatch_customer_message(self.rental, CustomerMessage.Kind.PICKUP_REMINDER)
        self.assertEqual(first.status, CustomerMessage.Status.FAILED)
        second = dispatch_customer_message(self.rental, CustomerMessage.Kind.PICKUP_REMINDER)
        self.assertEqual(second.status, CustomerMessage.Status.SENT)
        self.assertEqual(
            CustomerMessage.objects.filter(rental=self.rental).count(), 2
        )


class RentalDeletionTests(TestCase):
    """Regression test: deleting a rental that already has a CustomerMessage
    must not raise ProtectedError (bug seen in prod on 2026-07-20 — deleting
    a cancelled rental after a WhatsApp reminder had been sent to it returned
    a 500). ``CustomerMessage.rental`` uses SET_NULL, the same pattern as
    ``billing.Payment.rental``/``billing.FinancialMovement.rental``."""

    @mock.patch('notifications.services.evolution.send_text', return_value='MSGID')
    def test_deleting_rental_with_customer_message_sets_rental_null(self, send_text):
        _make_company()
        customer = _make_customer()
        rental = _make_rental(
            90001, customer, Rental.Status.CANCELLED, TODAY, TODAY + timedelta(days=2),
        )
        record = dispatch_customer_message(rental, CustomerMessage.Kind.RETURN_REMINDER)
        self.assertEqual(record.status, CustomerMessage.Status.SENT)

        rental.delete()

        record.refresh_from_db()
        self.assertIsNone(record.rental_id)
        self.assertEqual(record.customer_id, customer.pk)
