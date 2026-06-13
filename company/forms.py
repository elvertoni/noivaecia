from django import forms

from core.ui import INPUT_CLASS

from .models import Company


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = (
            'name', 'address', 'city', 'cnpj', 'phones',
            'last_rental_number', 'daily_interest_rate',
            'late_fee_rate', 'monthly_interest_rate',
            'damage_penalty_rate', 'loss_penalty_rate',
            'footer_message',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = INPUT_CLASS
