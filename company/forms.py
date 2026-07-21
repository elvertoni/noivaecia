import re

from django import forms
from django.core.exceptions import ValidationError

from core.ui import INPUT_CLASS, configure_br_decimal_field

from .models import Company

WHATSAPP_NUMBER_RE = re.compile(r'^55\d{10,11}$')
WHATSAPP_NUMBER_SPLIT_RE = re.compile(r'[,;\n]+')
WHATSAPP_NUMBER_START_RE = re.compile(
    r'(?<!^)\s+(?=(?:\+?55\d{10,11}\b|\+?55[\s(]))'
)


def _validate_cnpj(cnpj):
    """Validate the two CNPJ check digits for a 14-digit identifier."""
    if len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False

    for check_index, weights in (
        (12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
        (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
    ):
        total = sum(int(digit) * weight for digit, weight in zip(cnpj, weights))
        remainder = total % 11
        expected = 0 if remainder < 2 else 11 - remainder
        if expected != int(cnpj[check_index]):
            return False
    return True


def _format_cnpj(cnpj):
    return f'{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}'


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = (
            'name', 'address', 'city', 'cnpj', 'phones',
            'last_rental_number', 'daily_interest_rate',
            'late_fee_rate', 'monthly_interest_rate',
            'damage_penalty_rate', 'loss_penalty_rate',
            'footer_message',
            'whatsapp_reports_enabled', 'whatsapp_report_number',
            'whatsapp_report_time',
        )
        widgets = {
            'whatsapp_report_number': forms.Textarea(attrs={'rows': 3}),
            'whatsapp_report_time': forms.TimeInput(
                attrs={'type': 'time'}, format='%H:%M'
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.DecimalField):
                configure_br_decimal_field(field)
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            field.widget.attrs['class'] = INPUT_CLASS

        self.fields['cnpj'].widget.attrs.update({
            'placeholder': 'Ex.: 12.345.678/0001-95',
            'inputmode': 'numeric',
            'autocomplete': 'off',
        })
        self.fields['phones'].widget.attrs.update({
            'placeholder': 'Ex.: (43) 3542-1234',
            'type': 'tel',
            'inputmode': 'tel',
            'autocomplete': 'tel',
        })
        self.fields['whatsapp_report_number'].widget.attrs.update({
            'placeholder': 'Ex.: 5543999998888\n5543988887777',
            'inputmode': 'tel',
            'autocomplete': 'tel',
        })

    def clean_cnpj(self):
        raw = self.cleaned_data.get('cnpj', '').strip()
        if not raw:
            return ''
        if not re.fullmatch(r'[\d.\-/\s]+', raw):
            raise ValidationError('CNPJ inválido. Use apenas números e a pontuação do CNPJ.')
        digits = re.sub(r'\D', '', raw)
        if not _validate_cnpj(digits):
            raise ValidationError('CNPJ inválido. Verifique os dígitos informados.')
        return _format_cnpj(digits)

    def clean_whatsapp_report_number(self):
        raw = self.cleaned_data.get('whatsapp_report_number', '').strip()
        if not raw:
            return ''
        normalized = WHATSAPP_NUMBER_SPLIT_RE.sub('\n', raw)
        normalized = WHATSAPP_NUMBER_START_RE.sub('\n', normalized)
        numbers = []
        invalid_numbers = []
        for entry in normalized.splitlines():
            candidate = entry.strip()
            if not candidate:
                continue
            digits = re.sub(r'[\s\-().+]', '', candidate)
            if not digits.isdigit() or not WHATSAPP_NUMBER_RE.match(digits):
                invalid_numbers.append(candidate)
                continue
            if digits not in numbers:
                numbers.append(digits)
        if invalid_numbers:
            raise ValidationError(
                'Informe cada número com DDI 55, ex: 5543999998888.'
            )
        return '\n'.join(numbers)

    def clean(self):
        cleaned_data = super().clean()
        reports_enabled = cleaned_data.get('whatsapp_reports_enabled')
        numbers = (cleaned_data.get('whatsapp_report_number') or '').strip()
        if reports_enabled and not numbers:
            self.add_error(
                'whatsapp_report_number',
                'Informe ao menos um número que receberá o relatório diário.',
            )
        return cleaned_data
