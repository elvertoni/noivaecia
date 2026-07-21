"""Daily operational report builder for the WhatsApp integration (Fase 3).

Reuses the existing report/billing services wherever a matching function
exists, so this module never recomputes a business rule on its own:

- "Locações a retirar" (block b) reuses
  ``reports.services.report_a_retirar`` — the exact query backing the
  "A retirar" report screen — parametrized by ``on_date`` for both the
  today-list and the overdue count.
- "Valores a receber" (block c) reuses
  ``reports.services.report_contas_vencimento`` — the exact query backing
  the "Contas a vencer" report screen. No interest is recalculated here:
  ``Receivable.balance`` is already maintained by ``billing.models.Receivable``
  / ``billing.services.register_payment`` (write-offs force ``balance`` to
  zero, so the ``balance__gt=0`` filter already excludes them).
- "Entregas a fazer" (block a, devoluções) has no existing rental-level
  function that accepts an arbitrary reference date — the closest one,
  ``reports.services.report_atrasados``, hardcodes ``date.today()`` — so it
  filters ``Rental`` directly by ``status``/``return_date`` using the same
  semantics.
"""
import re
from datetime import date as date_cls, timedelta
from decimal import Decimal
from string import Formatter

from django.db import transaction
from django.utils import timezone

from notifications import evolution
from notifications.models import CustomerMessage
from reports.services import report_a_retirar, report_contas_vencimento
from rentals.models import Rental

MAX_ITEMS_PER_BLOCK = 15
_WEEKDAY_ABBR_PT = ('seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom')


def _format_brl(value):
    from core.templatetags.core_tags import brl
    return f'R$ {brl(value)}'


def _plural(count, singular, plural_form):
    return singular if count == 1 else plural_form


def _format_header(on_date):
    weekday = _WEEKDAY_ABBR_PT[on_date.weekday()]
    return f'🗓️ *Noivas & Cia — resumo de {weekday}, {on_date.strftime("%d/%m")}*'


def _truncate(lines):
    """Cap a block's item lines at ``MAX_ITEMS_PER_BLOCK``, adding a
    "+N outros" trailer when there are more."""
    if len(lines) <= MAX_ITEMS_PER_BLOCK:
        return lines
    shown = lines[:MAX_ITEMS_PER_BLOCK]
    shown.append(f'+{len(lines) - MAX_ITEMS_PER_BLOCK} outros')
    return shown


def _item_summary(rental):
    items = list(rental.items.all())
    if not items:
        return 'sem itens'
    summary = str(items[0].product)
    extra = len(items) - 1
    if extra:
        summary += f' +{extra}'
    return summary


def _deliveries_block(on_date):
    """Block (a): rentals picked up whose return is due ``on_date``, plus
    the count of overdue returns (``return_date < on_date``)."""
    qs = (
        Rental.objects.select_related('customer')
        .prefetch_related('items__product__category')
        .filter(status=Rental.Status.PICKED_UP, return_date=on_date)
        .order_by('number')
    )
    rentals = list(qs)
    lines = [
        f'• #{r.number} {r.customer.name} — {_item_summary(r)} '
        f'(retirado {r.pickup_date.strftime("%d/%m")})'
        for r in rentals
    ]
    overdue_count = Rental.objects.filter(
        status=Rental.Status.PICKED_UP, return_date__lt=on_date
    ).count()
    return _truncate(lines), len(rentals), overdue_count


def _pickups_block(on_date):
    """Block (b): rentals pending pickup ``on_date``, plus the count of
    overdue pickups (``pickup_date < on_date``)."""
    qs = report_a_retirar(date_from=on_date, date_to=on_date, max_results=None)
    rentals = list(qs)
    lines = [
        f'• #{r.number} {r.customer.name} — {_item_summary(r)}'
        for r in rentals
    ]
    yesterday = on_date - timedelta(days=1)
    overdue_count = report_a_retirar(date_to=yesterday, max_results=None).count()
    return _truncate(lines), len(rentals), overdue_count


def _receivables_block(on_date):
    """Block (c): open receivables due ``on_date``, plus the accumulated
    total/count of overdue open receivables (``due_date < on_date``)."""
    due_qs, due_totals = report_contas_vencimento(
        date_from=on_date, date_to=on_date, max_results=None
    )
    due_rows = list(due_qs)
    lines = [
        f'• #{rec.rental.number} {rec.rental.customer.name} — {_format_brl(rec.balance)}'
        for rec in due_rows
    ]
    due_total = due_totals['t_balance'] or Decimal('0')

    yesterday = on_date - timedelta(days=1)
    overdue_qs, overdue_totals = report_contas_vencimento(date_to=yesterday, max_results=None)
    overdue_count = overdue_qs.count()
    overdue_total = overdue_totals['t_balance'] or Decimal('0')

    return _truncate(lines), len(due_rows), due_total, overdue_count, overdue_total


def build_daily_report(on_date=None):
    """Assemble the WhatsApp text for the daily operational summary."""
    on_date = on_date or date_cls.today()
    header = _format_header(on_date)

    delivery_lines, delivery_count, delivery_overdue = _deliveries_block(on_date)
    pickup_lines, pickup_count, pickup_overdue = _pickups_block(on_date)
    (
        receivable_lines,
        receivable_count,
        receivable_total,
        overdue_receivable_count,
        overdue_receivable_total,
    ) = _receivables_block(on_date)

    nothing_to_report = not any([
        delivery_count, delivery_overdue,
        pickup_count, pickup_overdue,
        receivable_count, overdue_receivable_count,
    ])
    if nothing_to_report:
        return f'{header}\n\n✅ Sem entregas, retiradas ou vencimentos hoje.'

    parts = [header, '']

    parts.append(f'📦 *Entregas a fazer hoje: {delivery_count}*')
    parts.extend(delivery_lines)
    if delivery_overdue:
        word = _plural(delivery_overdue, 'devolução atrasada', 'devoluções atrasadas')
        parts.append(f'⚠️ {delivery_overdue} {word}')
    parts.append('')

    parts.append(f'👗 *Retiradas de hoje: {pickup_count}*')
    parts.extend(pickup_lines)
    if pickup_overdue:
        word = _plural(pickup_overdue, 'retirada atrasada', 'retiradas atrasadas')
        parts.append(f'⚠️ {pickup_overdue} {word}')
    parts.append('')

    titles_word = _plural(receivable_count, 'título', 'títulos')
    parts.append(
        f'💰 *A receber hoje: {_format_brl(receivable_total)} '
        f'({receivable_count} {titles_word})*'
    )
    parts.extend(receivable_lines)
    if overdue_receivable_count:
        overdue_titles_word = _plural(overdue_receivable_count, 'título', 'títulos')
        parts.append(
            f'Vencidos em aberto: {_format_brl(overdue_receivable_total)} '
            f'({overdue_receivable_count} {overdue_titles_word})'
        )

    return '\n'.join(parts).rstrip()


# ── Customer-facing WhatsApp messages (pickup/return reminders) ────────────
#
# These functions are the backend primitives for the customer-notification
# feature (see whats.md §9.1): a message on the eve of the pickup date and a
# reminder on the return date itself. The panel may provide an editable
# template; this module expands it, dispatches sends, and records every
# attempt in ``CustomerMessage``.


def format_whatsapp_number(digits):
    """Turn a customer's ``phone_mobile_digits`` into a Brazilian E.164
    number, or ``None`` when it cannot be made into a valid one.

    Rules: strip non-digits; a number already starting with ``55`` and 12-13
    digits long is used as-is; a bare 10 or 11 digit number (DDD+phone) gets
    ``55`` prefixed; anything else is invalid.
    """
    digits = re.sub(r'\D', '', digits or '')
    if digits.startswith('55') and len(digits) in (12, 13):
        return digits
    if len(digits) in (10, 11):
        return f'55{digits}'
    return None


def _first_name(customer):
    """First name, nicely capitalized (e.g. ``'MARIA SILVA'`` -> ``'Maria'``)."""
    name = (customer.name or '').strip()
    if not name:
        return ''
    return name.split()[0].capitalize()


PICKUP_MESSAGE_TEMPLATE = (
    'Oi, {cliente}! 💛 Aqui é a Ana, da Noivas & Cia. Passando pra avisar '
    'com carinho que a partir de amanhã, {data_retirada}, você já pode retirar '
    '{itens} aqui na loja. Está tudo pronto e esperando por você! Estamos '
    'felizes demais em fazer parte desse seu momento. Qualquer dúvida, é só me '
    'chamar por aqui. Um abraço carinhoso 🌸'
)

RETURN_MESSAGE_TEMPLATE = (
    'Oi, {cliente}! 💛 Aqui é a Ana, da Noivas & Cia. Espero de coração que '
    'seu evento tenha sido lindo! 🥂 Passei só pra lembrar, com todo carinho, '
    'que a devolução de {itens} está marcada para {data_devolucao}. Quando '
    'puder trazer, a gente agradece muito. Assim já fica tudo certinho pra '
    'encantar outra pessoa. Estou por aqui pro que precisar. Um beijo! 🌷'
)

MESSAGE_TEMPLATE_PLACEHOLDERS = {
    'cliente': 'primeiro nome da cliente',
    'numero_locacao': 'número da locação',
    'data_retirada': 'data de retirada (dd/mm)',
    'data_devolucao': 'data de devolução (dd/mm)',
    'itens': '“sua peça” ou “suas peças”',
}

_DEFAULT_MESSAGE_TEMPLATES = {
    CustomerMessage.Kind.PICKUP_REMINDER: PICKUP_MESSAGE_TEMPLATE,
    CustomerMessage.Kind.RETURN_REMINDER: RETURN_MESSAGE_TEMPLATE,
}


class MessageTemplateError(ValueError):
    """Raised when a message template contains an unsupported placeholder."""


def get_default_message_template(kind):
    """Return the editable default template for a customer message kind."""
    return _DEFAULT_MESSAGE_TEMPLATES[kind]


def validate_message_template(template):
    """Validate an editable WhatsApp template before any recipient is sent."""
    template = (template or '').strip()
    if not template:
        raise MessageTemplateError('Informe a mensagem que será enviada.')
    if len(template) > 2_000:
        raise MessageTemplateError('A mensagem pode ter no máximo 2.000 caracteres.')

    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise MessageTemplateError(
            'Revise as chaves da mensagem. Use chaves completas, como {cliente}.'
        ) from exc

    for _, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in MESSAGE_TEMPLATE_PLACEHOLDERS:
            raise MessageTemplateError(
                f'Placeholder inválido: {{{field_name}}}. '
                'Use apenas os placeholders informados no painel.'
            )
        if format_spec or conversion:
            raise MessageTemplateError(
                f'O placeholder {{{field_name}}} não aceita formatação adicional.'
            )

    return template


def _message_template_context(rental):
    item_count = rental.items.count()
    return {
        'cliente': _first_name(rental.customer),
        'numero_locacao': rental.number,
        'data_retirada': rental.pickup_date.strftime('%d/%m'),
        'data_devolucao': rental.return_date.strftime('%d/%m'),
        'itens': 'sua peça' if item_count <= 1 else 'suas peças',
    }


def render_message_template(rental, template):
    """Substitute the supported placeholders for a rental in ``template``."""
    template = validate_message_template(template)
    return template.format(**_message_template_context(rental))


def render_pickup_message(rental, template=None):
    """Render the eve-of-pickup WhatsApp message for ``rental``."""
    return render_message_template(
        rental,
        PICKUP_MESSAGE_TEMPLATE if template is None else template,
    )


def render_return_message(rental, template=None):
    """Render the return-day WhatsApp message for ``rental``."""
    return render_message_template(
        rental,
        RETURN_MESSAGE_TEMPLATE if template is None else template,
    )


def _already_sent_rental_ids(kind):
    return CustomerMessage.objects.filter(
        kind=kind, status=CustomerMessage.Status.SENT,
    ).values_list('rental_id', flat=True)


def pickup_reminder_queue(today=None):
    """Rentals eligible for the eve-of-pickup reminder: ``pending`` rentals
    whose ``pickup_date`` is tomorrow, with a valid phone and no prior
    ``SENT`` pickup reminder for that rental."""
    today = today or timezone.localdate()
    target_date = today + timedelta(days=1)
    rentals = (
        Rental.objects.filter(status=Rental.Status.PENDING, pickup_date=target_date)
        .exclude(pk__in=_already_sent_rental_ids(CustomerMessage.Kind.PICKUP_REMINDER))
        .select_related('customer')
        .order_by('customer__name')
    )
    queue = []
    for rental in rentals:
        phone = format_whatsapp_number(rental.customer.phone_mobile_digits)
        if not phone:
            continue
        queue.append({
            'rental': rental,
            'customer': rental.customer,
            'phone': phone,
            'message': render_pickup_message(rental),
        })
    return queue


def return_reminder_queue(today=None):
    """Rentals eligible for the return-day reminder: ``picked_up`` rentals
    whose ``return_date`` is today, with a valid phone and no prior ``SENT``
    return reminder for that rental."""
    today = today or timezone.localdate()
    rentals = (
        Rental.objects.filter(status=Rental.Status.PICKED_UP, return_date=today)
        .exclude(pk__in=_already_sent_rental_ids(CustomerMessage.Kind.RETURN_REMINDER))
        .select_related('customer')
        .order_by('customer__name')
    )
    queue = []
    for rental in rentals:
        phone = format_whatsapp_number(rental.customer.phone_mobile_digits)
        if not phone:
            continue
        queue.append({
            'rental': rental,
            'customer': rental.customer,
            'phone': phone,
            'message': render_return_message(rental),
        })
    return queue


_MESSAGE_RENDERERS = {
    CustomerMessage.Kind.PICKUP_REMINDER: render_pickup_message,
    CustomerMessage.Kind.RETURN_REMINDER: render_return_message,
}


def dispatch_customer_message(rental, kind, user=None, message_template=None):
    """Send (or record the failure to send) a customer WhatsApp message for
    ``rental``, and return the resulting ``CustomerMessage``.

    Idempotent: a rental that already has a ``SENT`` ``CustomerMessage`` for
    ``kind`` is returned as-is, without calling the Evolution API again.
    An invalid phone number short-circuits to a ``FAILED`` record without any
    network call.
    """
    with transaction.atomic():
        existing = CustomerMessage.objects.filter(
            rental=rental, kind=kind, status=CustomerMessage.Status.SENT,
        ).first()
        if existing:
            return existing

        customer = rental.customer
        render = _MESSAGE_RENDERERS[kind]
        phone = format_whatsapp_number(customer.phone_mobile_digits)

        if not phone:
            return CustomerMessage.objects.create(
                rental=rental,
                customer=customer,
                kind=kind,
                phone=customer.phone_mobile_digits or '',
                status=CustomerMessage.Status.FAILED,
                error='telefone inválido',
                sent_by=user,
            )

        message = render(rental, message_template)
        try:
            message_id = evolution.send_text(phone, message)
        except evolution.EvolutionError as exc:
            return CustomerMessage.objects.create(
                rental=rental,
                customer=customer,
                kind=kind,
                phone=phone,
                status=CustomerMessage.Status.FAILED,
                error=str(exc),
                sent_by=user,
            )

        return CustomerMessage.objects.create(
            rental=rental,
            customer=customer,
            kind=kind,
            phone=phone,
            status=CustomerMessage.Status.SENT,
            message_id=str(message_id),
            sent_at=timezone.now(),
            sent_by=user,
        )
