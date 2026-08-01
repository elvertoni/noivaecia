from decimal import Decimal

from django import forms
from django.utils import timezone

from billing.models import Payment
from core.ui import (
    BRMoneyField,
    DATE_INPUT_ATTRS,
    DATE_INPUT_FORMATS,
    INPUT_CLASS,
    configure_br_decimal_field,
)

from .models import Pickup, Return


class PickupForm(forms.ModelForm):
    class Meta:
        model = Pickup
        fields = ('pickup_date',)
        widgets = {'pickup_date': forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy())}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pickup_date'].input_formats = DATE_INPUT_FORMATS
        self.fields['pickup_date'].widget.attrs['class'] = INPUT_CLASS

    def clean_pickup_date(self):
        pickup_date = self.cleaned_data['pickup_date']
        if pickup_date > timezone.localdate():
            raise forms.ValidationError(
                'A retirada não pode ser registrada em uma data futura.'
            )
        return pickup_date


class ReturnForm(forms.ModelForm):
    """Return form. days_late and penalty_applied are computed in the view."""

    payment_amount = BRMoneyField(
        label='Valor recebido agora', required=False, min_value=Decimal('0.01'),
        decimal_places=2, max_digits=10,
    )
    payment_method = forms.ChoiceField(
        label='Forma de recebimento', required=False,
        choices=[
            ('', 'Não registrar recebimento'),
        ] + list(Payment.Method.choices),
    )
    payment_date = forms.DateField(
        label='Data do recebimento', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )

    class Meta:
        model = Return
        fields = ('return_date', 'damage_amount', 'damage_notes')
        widgets = {'return_date': forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy())}

    def __init__(self, *args, rental=None, **kwargs):
        self.rental = rental
        super().__init__(*args, **kwargs)
        self.fields['return_date'].input_formats = DATE_INPUT_FORMATS
        self.fields['return_date'].widget.attrs['class'] = INPUT_CLASS
        configure_br_decimal_field(self.fields['damage_amount'], currency=True)
        self.fields['damage_amount'].min_value = Decimal('0')
        self.fields['damage_amount'].widget.attrs['class'] = INPUT_CLASS
        self.fields['damage_notes'].widget.attrs.setdefault('rows', 2)
        self.fields['damage_notes'].widget.attrs['class'] = INPUT_CLASS
        self.fields['payment_amount'].widget.attrs['class'] = INPUT_CLASS
        self.fields['payment_method'].widget.attrs['class'] = INPUT_CLASS
        self.fields['payment_date'].widget.attrs['class'] = INPUT_CLASS

    def clean(self):
        cleaned_data = super().clean()
        payment_amount = cleaned_data.get('payment_amount')
        payment_method = cleaned_data.get('payment_method')
        return_date = cleaned_data.get('return_date')
        payment_date = cleaned_data.get('payment_date')
        today = timezone.localdate()
        if return_date and return_date > today:
            self.add_error(
                'return_date',
                'A devolução não pode ser registrada em uma data futura.',
            )
        if payment_date and payment_date > today:
            self.add_error(
                'payment_date',
                'O recebimento não pode ser registrado em uma data futura.',
            )
        if payment_amount and payment_amount > Decimal('0') and not payment_method:
            self.add_error('payment_method', 'Selecione a forma de recebimento.')
        if payment_method and not payment_amount and 'payment_amount' not in self.errors:
            self.add_error('payment_amount', 'Informe um valor para registrar o recebimento.')
        if self.rental and return_date and hasattr(self.rental, 'pickup'):
            if return_date < self.rental.pickup.pickup_date:
                self.add_error(
                    'return_date',
                    'A data de devolução não pode ser anterior à retirada registrada.',
                )
        return cleaned_data
