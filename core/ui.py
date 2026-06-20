"""Shared CSS classes and widgets for server-rendered UI components."""

from django import forms

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
