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
from datetime import date as date_cls, timedelta
from decimal import Decimal

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
