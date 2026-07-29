"""Tests for shared server-rendered UI helpers."""

from decimal import Decimal

from django import forms
from django.test import SimpleTestCase

from core.templatetags.core_tags import render_field
from core.ui import (
    BRMoneyField,
    configure_br_decimal_field,
    normalize_br_decimal,
    parse_br_date,
)


class BrazilianDecimalTests(SimpleTestCase):
    def test_normalizes_supported_brazilian_and_dot_decimal_formats(self):
        cases = {
            '1.234,56': '1234.56',
            '1234,56': '1234.56',
            '1,234.56': '1234.56',
            '1234.56': '1234.56',
            '1.234': '1234',
            'R$ 1.234,56': '1234.56',
        }

        for raw_value, normalized_value in cases.items():
            with self.subTest(raw_value=raw_value):
                self.assertEqual(normalize_br_decimal(raw_value), normalized_value)

    def test_keeps_invalid_value_for_standard_django_validation(self):
        self.assertEqual(normalize_br_decimal('1.23,45'), '1.23,45')

    def test_money_field_validates_brazilian_formatted_value_without_javascript(self):
        field = BRMoneyField(max_digits=10, decimal_places=2)

        self.assertEqual(field.clean('R$ 1.234,56'), Decimal('1234.56'))

    def test_money_field_renders_prefix_and_accessible_description(self):
        field = BRMoneyField(max_digits=10, decimal_places=2)

        rendered = field.widget.render('amount', Decimal('1234.5'), {'id': 'id_amount'})

        self.assertIn('class="currency-field"', rendered)
        self.assertIn('class="currency-prefix"', rendered)
        self.assertIn('1.234,50', rendered)
        self.assertIn('aria-describedby="id_amount-currency"', rendered)
        self.assertIn('id="id_amount-currency"', rendered)

    def test_configure_decimal_field_preserves_existing_widget_attributes(self):
        field = forms.DecimalField(
            widget=forms.NumberInput(attrs={
                'min': '0',
                'placeholder': 'Valor',
            }),
        )

        configure_br_decimal_field(field, currency=True)

        self.assertEqual(field.widget.attrs['min'], '0')
        self.assertEqual(field.widget.attrs['placeholder'], 'Valor')

    def test_render_field_merges_existing_accessibility_descriptions(self):
        class AmountForm(forms.Form):
            amount = BRMoneyField(
                max_digits=10,
                decimal_places=2,
                help_text='Informe o valor.',
            )

        form = AmountForm()
        form.fields['amount'].widget.attrs['aria-describedby'] = 'custom-help'

        rendered = render_field(form['amount'])

        self.assertIn(
            'aria-describedby="custom-help id_amount-help id_amount-currency"',
            rendered,
        )


class BrazilianDateTests(SimpleTestCase):
    def test_parses_iso_and_brazilian_dates(self):
        self.assertEqual(parse_br_date('2026-07-20').isoformat(), '2026-07-20')
        self.assertEqual(parse_br_date('20/07/2026').isoformat(), '2026-07-20')
        self.assertEqual(parse_br_date('20/07/26').isoformat(), '2026-07-20')

    def test_returns_none_for_invalid_date_values(self):
        self.assertIsNone(parse_br_date('31/02/2026'))
        self.assertIsNone(parse_br_date('20-07-2026'))
