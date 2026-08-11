import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from core.ui import BRMoneyField, DATE_INPUT_ATTRS, DATE_INPUT_FORMATS, INPUT_CLASS

from .models import CashAccount, FinancialMovement, Payment


def _reject_future_date(value, label):
    if value > timezone.localdate():
        raise forms.ValidationError(f'A {label} não pode estar no futuro.')
    return value


class SubmissionTokenForm(forms.Form):
    """Carry a per-render token so a resubmitted page is the same cash event.

    The token is minted when the page is rendered and posted back untouched, so
    a double click (or a browser retry) reaches the service with the same
    idempotency key and registers the money exactly once. It is never trusted
    as a key on its own — views derive a UUID5 from it under their own
    namespace.
    """

    submission_token = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        initial=lambda: str(uuid.uuid4()),
    )

    def clean_submission_token(self):
        """Fall back to a fresh token instead of rejecting the receipt.

        A stale page or a stripped field only costs the double-submit guard;
        refusing the submission would cost the operator a real payment.
        """
        raw_token = (self.cleaned_data.get('submission_token') or '').strip()
        try:
            return str(uuid.UUID(raw_token))
        except (AttributeError, TypeError, ValueError):
            return str(uuid.uuid4())


class GenerateReceivablesForm(forms.Form):
    """Generate N installments for a rental (RF-19)."""

    installments = forms.IntegerField(
        label='Número de parcelas futuras', min_value=1, max_value=9, initial=1,
        widget=forms.NumberInput(attrs={'class': INPUT_CLASS}),
    )
    first_due_date = forms.DateField(
        label='Primeiro vencimento futuro', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )


class PaymentForm(SubmissionTokenForm):
    """Register a payment against a receivable (RF-21)."""

    value = BRMoneyField(
        label='Valor recebido', min_value=Decimal('0.01'), max_digits=10, decimal_places=2,
    )
    payment_date = forms.DateField(
        label='Data do recebimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs=DATE_INPUT_ATTRS.copy()),
        input_formats=DATE_INPUT_FORMATS,
    )

    def clean_payment_date(self):
        return _reject_future_date(
            self.cleaned_data['payment_date'],
            'data do recebimento',
        )


class ReceivablePayForm(SubmissionTokenForm):
    """Enhanced payment form that creates a Payment record (R5.06/R5.08)."""

    amount = BRMoneyField(
        label='Valor recebido', min_value=Decimal('0.01'), max_digits=10, decimal_places=2,
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
    def clean_payment_date(self):
        return _reject_future_date(
            self.cleaned_data['payment_date'],
            'data do recebimento',
        )


class ReversalForm(SubmissionTokenForm):
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
        initial=timezone.localdate,
        input_formats=DATE_INPUT_FORMATS,
    )

    def clean_date(self):
        return _reject_future_date(
            self.cleaned_data['date'],
            'data do movimento',
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
        help_text='Informe o nome completo exatamente como está cadastrado.',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )

    def clean_customer_name(self):
        """Link manual movements only to an unambiguous registered customer."""
        from customers.models import Customer, _normalize_name

        customer_name = self.cleaned_data['customer_name'].strip()
        if not customer_name:
            self.cleaned_data['customer'] = None
            return customer_name

        customers = list(
            Customer.objects.filter(
                name_search=_normalize_name(customer_name),
            )[:2]
        )
        if not customers:
            raise forms.ValidationError(
                'Cliente não encontrado. Informe o nome completo cadastrado ou deixe o campo em branco.'
            )
        if len(customers) > 1:
            raise forms.ValidationError(
                'Há mais de um cliente com este nome. Deixe o campo em branco e registre o vínculo depois.'
            )

        self.cleaned_data['customer'] = customers[0]
        return customer_name


class MultiPayForm(SubmissionTokenForm):
    """Multi-receivable payment form (R5.07)."""

    total_amount = BRMoneyField(
        label='Valor total a receber', min_value=Decimal('0.01'), max_digits=10, decimal_places=2,
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

    def clean_payment_date(self):
        return _reject_future_date(
            self.cleaned_data['payment_date'],
            'data do recebimento',
        )
