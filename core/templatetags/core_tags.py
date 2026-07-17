from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template
from django.utils.safestring import mark_safe

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
def render_field(field):
    """Render a bound field with shared accessibility attributes."""
    described_by = []
    if field.help_text:
        described_by.append(f'{field.auto_id}-help')
    if field.errors:
        described_by.append(f'{field.auto_id}-error')

    attrs = {}
    if field.errors:
        attrs['aria-invalid'] = 'true'
    if described_by:
        attrs['aria-describedby'] = ' '.join(described_by)

    return mark_safe(field.as_widget(attrs=attrs))
