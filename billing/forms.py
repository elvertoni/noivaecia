from django import forms

from core.ui import INPUT_CLASS


class GenerateReceivablesForm(forms.Form):
    """Generate N installments for a rental (RF-19)."""

    installments = forms.IntegerField(
        label='Número de parcelas', min_value=1, initial=1,
        widget=forms.NumberInput(attrs={'class': INPUT_CLASS}),
    )
    first_due_date = forms.DateField(
        label='Primeiro vencimento', required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': INPUT_CLASS}),
    )


class PaymentForm(forms.Form):
    """Register a payment against a receivable (RF-21)."""

    value = forms.DecimalField(
        label='Valor pago', min_value=0, max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': INPUT_CLASS, 'step': '0.01'}),
    )
    payment_date = forms.DateField(
        label='Data do pagamento',
        widget=forms.DateInput(attrs={'type': 'date', 'class': INPUT_CLASS}),
    )
