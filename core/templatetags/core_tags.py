from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template
from django.utils.safestring import mark_safe

from core.ui import parse_br_date

register = template.Library()


@register.filter
def has_module(user, module_key):
    """Template helper mirroring ``accounts.User.has_module``.

    Returns False for anonymous users so navigation entries hide cleanly.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return user.has_module(module_key)


@register.filter
def has_action(user, action_key):
    """Template helper mirroring ``accounts.User.has_action`` (R12.02)."""
    if not getattr(user, 'is_authenticated', False):
        return False
    return user.has_action(action_key)


@register.filter
def brl(value):
    """Format a numeric amount in Brazilian style: 1234567.5 -> '1.234.567,50'.

    Drop-in replacement for ``floatformat:2`` in money displays; keeps the
    caller's ``R$`` prefix. Non-numeric input is returned unchanged.
    """
    if value is None or value == '':
        value = 0
    try:
        amount = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return value
    grouped = f'{amount:,.2f}'
    return grouped.replace(',', '\x00').replace('.', ',').replace('\x00', '.')


@register.filter
def pct(value):
    """Format a percentage rate: 50 -> '50', 12.5 -> '12,5', 0 -> '0'.

    Rates are stored as DecimalField(decimal_places=2), so ``brl`` would print
    a contract clause as '50,00%'. Trailing zeros are dropped so the printed
    contract reads the way the rate was configured.
    """
    if value is None or value == '':
        value = 0
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    rate = rate.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP).normalize()
    if rate == rate.to_integral_value():
        rate = rate.to_integral_value()
    return f'{rate:f}'.replace('.', ',')


@register.filter
def receipt_status(receivable):
    """Per-installment status label for the printed contract's payment matrix."""
    if not receivable:
        return ''
    if receivable.is_written_off:
        return 'Baixada'
    if receivable.is_paid:
        if receivable.last_payment_date:
            return f'Quitada em {receivable.last_payment_date:%d/%m/%Y}'
        return 'Quitada'
    if receivable.paid_amount:
        if receivable.last_payment_date:
            return f'Recebida parcialmente em {receivable.last_payment_date:%d/%m/%Y}'
        return 'Recebida parcialmente'
    return 'Em aberto'


@register.filter
def br_date_text(value):
    """Display ISO/BR date strings as dd/mm/YYYY, leaving legacy text intact."""
    parsed = parse_br_date(value)
    if parsed:
        return parsed.strftime('%d/%m/%Y')
    return value


@register.filter
def render_field(field):
    """Render a bound field with shared accessibility attributes."""
    described_by = (
        field.field.widget.attrs.get('aria-describedby', '').split()
    )
    if field.help_text:
        described_by.append(f'{field.auto_id}-help')
    if field.errors:
        described_by.append(f'{field.auto_id}-error')
    described_by = list(dict.fromkeys(described_by))

    attrs = {}
    if field.errors:
        attrs['aria-invalid'] = 'true'
    if described_by:
        attrs['aria-describedby'] = ' '.join(described_by)

    return mark_safe(field.as_widget(attrs=attrs))
