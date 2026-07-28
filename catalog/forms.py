from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator

from core.ui import INPUT_CLASS, configure_br_decimal_field

from .models import Category, Product


def _style_fields(form):
    for field_name, field in form.fields.items():
        if isinstance(field.widget, forms.Textarea):
            field.widget.attrs.setdefault('rows', 3)
        if isinstance(field, forms.DecimalField):
            configure_br_decimal_field(field, currency=field_name == 'value')
        field.widget.attrs['class'] = INPUT_CLASS


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ('prefix', 'name')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self)

    def clean_prefix(self):
        """Keep category identifiers canonical and unambiguous.

        Availability and product lookup treat prefixes case-insensitively.  A
        case-sensitive duplicate therefore looks like one category in the UI
        while remaining two distinct records in the database.
        """
        prefix = self.cleaned_data['prefix'].upper()
        duplicate = Category.objects.filter(prefix__iexact=prefix)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise ValidationError('Já existe uma categoria com este prefixo.')
        return prefix


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ('category', 'code', 'description', 'color', 'size', 'value', 'notes')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # R8.07 — make clear Product.value is a suggestion, not copied to existing rentals
        self.fields['value'].help_text = (
            'Valor sugerido para novas locações. Não altera o valor já cobrado em locações existentes.'
        )
        _style_fields(self)
        self.fields['value'].min_value = Decimal('0')
        self.fields['value'].validators.append(MinValueValidator(Decimal('0')))


class AvailabilityQueryForm(forms.Form):
    """Validate the fast prefix/code availability lookup.

    The date remains optional for compatibility with date-specific checks.  In
    the operational flow the view uses the current local date when it is empty.
    """

    prefix = forms.CharField(
        label='Prefixo',
        max_length=10,
        widget=forms.TextInput(attrs={
            'autocomplete': 'off',
            'autocapitalize': 'characters',
            'autofocus': True,
            'data-enter-next': 'availability-code',
            'id': 'availability-prefix',
            'placeholder': 'Ex.: VF',
        }),
    )
    code = forms.CharField(
        label='Código',
        max_length=10,
        widget=forms.TextInput(attrs={
            'autocomplete': 'off',
            'data-submit-on-enter': 'true',
            'id': 'availability-code',
            'inputmode': 'numeric',
            'pattern': '[0-9]*',
            'placeholder': 'Ex.: 38',
        }),
    )
    date = forms.DateField(
        label='Data de consulta',
        required=False,
        input_formats=('%d/%m/%Y', '%Y-%m-%d'),
        error_messages={'invalid': 'Informe uma data válida.'},
        help_text='Deixe em branco para consultar a disponibilidade de hoje.',
        widget=forms.TextInput(attrs={
            'autocomplete': 'off',
            'data-date-br': 'true',
            'id': 'availability-date',
            'inputmode': 'numeric',
            'maxlength': '10',
            'placeholder': 'dd/mm/aaaa',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self)

    def clean_prefix(self):
        return self.cleaned_data['prefix'].strip().upper()

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if not code.isdigit():
            raise ValidationError('Informe um código de produto válido.')

        numeric_code = code.lstrip('0') or '0'
        if len(numeric_code) > 10 or int(numeric_code) > 2147483647:
            raise ValidationError('Informe um código de produto válido.')
        return int(numeric_code)


class CategoryMergeForm(forms.Form):
    """Select source and target for category merge (R8.06)."""

    source = forms.ModelChoiceField(
        queryset=Category.objects.all().order_by('prefix'),
        label='Categoria de origem (será esvaziada)',
        help_text='Todos os produtos desta categoria serão movidos para a categoria destino.',
    )
    target = forms.ModelChoiceField(
        queryset=Category.objects.all().order_by('prefix'),
        label='Categoria destino',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self)

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get('source')
        target = cleaned.get('target')
        if source and target and source == target:
            raise ValidationError('Categoria de origem e destino não podem ser iguais.')
        return cleaned
