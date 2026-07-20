import re

from django import forms
from django.core.exceptions import ValidationError

from core.ui import BRDecimalInput, INPUT_CLASS

from .models import Company

WHATSAPP_NUMBER_RE = re.compile(r'^55\d{10,11}$')


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
            'whatsapp_report_time': forms.TimeInput(attrs={'type': 'time'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.DecimalField) and not isinstance(field.widget, BRDecimalInput):
                field.widget = BRDecimalInput()
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            field.widget.attrs['class'] = INPUT_CLASS

    def clean_whatsapp_report_number(self):
        raw = self.cleaned_data.get('whatsapp_report_number', '').strip()
        if not raw:
            return ''
        digits = re.sub(r'[\s\-().+]', '', raw)
        if not digits.isdigit() or not WHATSAPP_NUMBER_RE.match(digits):
            raise ValidationError('Informe o número com DDI, ex: 5543999998888.')
        return digits

