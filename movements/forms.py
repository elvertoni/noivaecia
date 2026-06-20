from decimal import Decimal

from django import forms

from core.ui import BRDecimalInput, DATE_INPUT_ATTRS, DATE_INPUT_FORMATS, INPUT_CLASS

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


class ReturnForm(forms.ModelForm):
    """Return form. days_late and penalty_applied are computed in the view."""

    payment_amount = forms.DecimalField(
        label='Valor recebido agora', required=False, min_value=Decimal('0'),
        decimal_places=2, max_digits=10,
        widget=BRDecimalInput(),
    )
    payment_method = forms.ChoiceField(
        label='Forma de pagamento', required=False,
        choices=[
            ('', 'Não registrar pagamento'),
            ('cash', 'Dinheiro'),
            ('pix', 'Pix'),
            ('card_debit', 'Débito'),
            ('card_credit', 'Crédito'),
            ('transfer', 'Transferência'),
            ('other', 'Outro'),
        ],
    )
    payment_date = forms.DateField(
        label='Data do pagamento', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )

    class Meta:
        model = Return
        fields = ('return_date',)
        widgets = {'return_date': forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy())}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['return_date'].input_formats = DATE_INPUT_FORMATS
        self.fields['return_date'].widget.attrs['class'] = INPUT_CLASS
        self.fields['payment_amount'].widget.attrs['class'] = INPUT_CLASS
        self.fields['payment_method'].widget.attrs['class'] = INPUT_CLASS
        self.fields['payment_date'].widget.attrs['class'] = INPUT_CLASS

    def clean(self):
        cleaned_data = super().clean()
        payment_amount = cleaned_data.get('payment_amount')
        payment_method = cleaned_data.get('payment_method')
        if payment_amount and payment_amount > Decimal('0') and not payment_method:
            self.add_error('payment_method', 'Selecione a forma de pagamento.')
        return cleaned_data
