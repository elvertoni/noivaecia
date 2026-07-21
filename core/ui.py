"""Shared formatting helpers and widgets for server-rendered UI components."""

import re
from datetime import date as date_cls

from django import forms
from django.utils.html import format_html

INPUT_CLASS = 'field-input'
DATE_INPUT_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y')
DATE_INPUT_ATTRS = {'type': 'date', 'class': INPUT_CLASS, 'data-date-br': 'true'}
DECIMAL_INPUT_ATTRS = {
    'type': 'text',
    'inputmode': 'decimal',
    'autocomplete': 'off',
    'data-decimal-br': 'true',
    'class': INPUT_CLASS,
}


def parse_br_date(value):
    """Return a valid date from ISO or Brazilian input, otherwise ``None``.

    Native date inputs submit ISO values, while the progressive enhancement in
    ``app.js`` displays Brazilian dates. Filters must safely support either
    representation because they do not have a Django form to validate them.
    """
    if isinstance(value, date_cls):
        return value
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    iso_match = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', raw)
    br_match = re.fullmatch(r'(\d{2})/(\d{2})/(\d{2}|\d{4})', raw)
    if iso_match:
        year, month, day = (int(part) for part in iso_match.groups())
    elif br_match:
        day, month, year = (int(part) for part in br_match.groups())
        if year < 100:
            year += 2000 if year <= 69 else 1900
    else:
        return None

    try:
        return date_cls(year, month, day)
    except ValueError:
        return None


class BRDecimalInput(forms.TextInput):
    """Text input that formats decimals for Brazilian locale (1.234,56).

    The companion JavaScript in app.js listens for ``data-decimal-br``
    and applies live masking + submit-time normalisation back to dot-decimal
    so the Django backend receives valid ``Decimal``-friendly strings.
    """

    def __init__(self, attrs=None):
        defaults = DECIMAL_INPUT_ATTRS.copy()
        if attrs:
            defaults.update(attrs)
        super().__init__(attrs=defaults)

    def format_value(self, value):
        """Render the initial value in pt-BR format (comma as decimal sep)."""
        if value is None or value == '':
            return ''
        try:
            # Accept both str and Decimal/float
            from decimal import Decimal, InvalidOperation
            d = Decimal(str(value))
            # Format with Brazilian separators: thousands='.', decimal=','
            sign, digits, exponent = d.as_tuple()
            # Normalise to 2 decimal places for money fields
            d_str = f'{abs(d):,.2f}'
            # Python's format uses US locale (1,234.56), swap separators
            # Step 1: comma -> temp; Step 2: dot -> comma; Step 3: temp -> dot
            d_str = d_str.replace(',', '_').replace('.', ',').replace('_', '.')
            if sign:
                d_str = '-' + d_str
            return d_str
        except (InvalidOperation, ValueError, TypeError):
            return str(value)


class BRMoneyInput(BRDecimalInput):
    """Brazilian decimal input with a visible and accessible BRL prefix."""

    def __init__(self, attrs=None):
        defaults = {'data-currency-br': 'true'}
        if attrs:
            defaults.update(attrs)
        super().__init__(attrs=defaults)

    def render(self, name, value, attrs=None, renderer=None):
        attrs = dict(attrs or {})
        input_id = attrs.get('id')
        currency_description_id = None
        if input_id:
            currency_description_id = f'{input_id}-currency'
            described_by = attrs.get('aria-describedby', '').split()
            if currency_description_id not in described_by:
                described_by.append(currency_description_id)
            attrs['aria-describedby'] = ' '.join(described_by)

        input_html = super().render(name, value, attrs, renderer)
        description = (
            format_html('<span id="{}" class="sr-only">Valor em reais</span>', currency_description_id)
            if currency_description_id else ''
        )
        return format_html(
            '<span class="currency-field"><span class="currency-prefix" aria-hidden="true">R$</span>{}{}</span>',
            input_html,
            description,
        )


def normalize_br_decimal(value):
    """Return a Decimal-compatible value for pt-BR and dot-decimal inputs.

    The browser formats values for pt-BR, but server validation must accept
    the same value when JavaScript is unavailable or a value is pasted.
    Invalid formats are deliberately left unchanged so Django can surface its
    standard validation error instead of silently changing a monetary amount.
    """
    if not isinstance(value, str):
        return value

    raw = value.strip().replace('\u00a0', ' ')
    if not raw:
        return ''
    raw = re.sub(r'^R\$\s*', '', raw, flags=re.IGNORECASE)
    raw = raw.replace(' ', '')

    br_number = r'[+-]?(?:\d{1,3}(?:\.\d{3})*|\d+)(?:,\d*)?'
    us_number = r'[+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d*)?'
    br_thousands = r'[+-]?\d{1,3}(?:\.\d{3})+'

    if ',' in raw and '.' in raw:
        if raw.rfind(',') > raw.rfind('.') and re.fullmatch(br_number, raw):
            return raw.replace('.', '').replace(',', '.')
        if raw.rfind('.') > raw.rfind(',') and re.fullmatch(us_number, raw):
            return raw.replace(',', '')
        return value
    if ',' in raw:
        return raw.replace(',', '.') if re.fullmatch(br_number, raw) else value
    if '.' in raw and re.fullmatch(br_thousands, raw):
        return raw.replace('.', '')
    if re.fullmatch(us_number, raw):
        return raw
    return value


class BRDecimalField(forms.DecimalField):
    """Decimal field that validates Brazilian-formatted values server-side."""

    def to_python(self, value):
        return super().to_python(normalize_br_decimal(value))


class BRMoneyField(BRDecimalField):
    """Brazilian decimal field for monetary amounts in reais."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', BRMoneyInput())
        super().__init__(*args, **kwargs)


def configure_br_decimal_field(field, *, currency=False):
    """Apply Brazilian parsing and the appropriate widget to model form fields."""
    if not isinstance(field, forms.DecimalField):
        return

    if not getattr(field, '_br_decimal_parser_enabled', False):
        original_to_python = field.to_python

        def to_python(value):
            return original_to_python(normalize_br_decimal(value))

        field.to_python = to_python
        field._br_decimal_parser_enabled = True

    widget_class = BRMoneyInput if currency else BRDecimalInput
    if not isinstance(field.widget, widget_class):
        field.widget = widget_class()
