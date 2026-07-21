from datetime import date as date_cls
from decimal import Decimal

from django import forms

from core.ui import BRMoneyField, DATE_INPUT_ATTRS, DATE_INPUT_FORMATS, INPUT_CLASS

from .models import CashAccount, FinancialMovement, Payment


class GenerateReceivablesForm(forms.Form):
    """Generate N installments for a rental (RF-19)."""

    installments = forms.IntegerField(
        label='Número de parcelas', min_value=1, initial=1,
        widget=forms.NumberInput(attrs={'class': INPUT_CLASS}),
    )
    first_due_date = forms.DateField(
        label='Primeiro vencimento', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )


class PaymentForm(forms.Form):
    """Register a payment against a receivable (RF-21)."""

    value = BRMoneyField(
        label='Valor recebido', min_value=0, max_digits=10, decimal_places=2,
    )
    payment_date = forms.DateField(
        label='Data do recebimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )


class ReceivablePayForm(forms.Form):
    """Enhanced payment form that creates a Payment record (R5.06/R5.08)."""

    amount = BRMoneyField(
        label='Valor recebido', min_value=0, max_digits=10, decimal_places=2,
    )
    payment_date = forms.DateField(
        label='Data do recebimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )
    method = forms.ChoiceField(
        label='Forma de recebimento',
        choices=Payment.Method.choices,
        widget=forms.Select(attrs={'class': INPUT_CLASS}),
        initial='cash',
    )
    interest_amount = BRMoneyField(
        label='Juros', min_value=0, max_digits=10, decimal_places=2,
        required=False, initial=0,
    )
    discount_amount = BRMoneyField(
        label='Desconto', min_value=0, max_digits=10, decimal_places=2,
        required=False, initial=0,
    )
    notes = forms.CharField(
        label='Observações', required=False,
        widget=forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': 2}),
    )
    confirm_overpayment = forms.BooleanField(
        label='Confirmar recebimento acima do saldo', required=False,
    )


class ReversalForm(forms.Form):
    """Reversal reason form (R5.09)."""

    reason = forms.CharField(
        label='Motivo do estorno',
        widget=forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': 3}),
    )


class ManualMovementForm(forms.Form):
    """Create a manual FinancialMovement (R6.02)."""

    date = forms.DateField(
        label='Data',
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        initial=date_cls.today,
        input_formats=DATE_INPUT_FORMATS,
    )
    account = forms.ModelChoiceField(
        label='Conta', queryset=CashAccount.objects.filter(active=True),
        widget=forms.Select(attrs={'class': INPUT_CLASS}),
    )
    direction = forms.ChoiceField(
        label='Direção',
        choices=FinancialMovement.Direction.choices,
        widget=forms.Select(attrs={'class': INPUT_CLASS}),
    )
    amount = BRMoneyField(
        label='Valor', min_value=Decimal('0.01'), max_digits=10, decimal_places=2,
    )
    description = forms.CharField(
        label='Histórico', max_length=500,
        widget=forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': 2}),
    )
    customer_name = forms.CharField(
        label='Cliente (opcional)', required=False,
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )


class MultiPayForm(forms.Form):
    """Multi-receivable payment form (R5.07)."""

    total_amount = BRMoneyField(
        label='Valor total a receber', min_value=0, max_digits=10, decimal_places=2,
    )
    payment_date = forms.DateField(
        label='Data do recebimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )
    method = forms.ChoiceField(
        label='Forma de recebimento',
        choices=Payment.Method.choices,
        widget=forms.Select(attrs={'class': INPUT_CLASS}),
        initial='cash',
    )
    notes = forms.CharField(
        label='Observações', required=False,
        widget=forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': 2}),
    )
