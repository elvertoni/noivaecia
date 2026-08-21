from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator

from core.ui import DATE_INPUT_FORMATS, INPUT_CLASS, configure_br_decimal_field

from .models import Category, Product
from .services import is_free_code_slot


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
        prefix = self.cleaned_data['prefix'].strip().upper()
        duplicate = Category.objects.filter(prefix__iexact=prefix)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise ValidationError('Já existe uma categoria com este prefixo.')
        return prefix


class ProductForm(forms.ModelForm):
    """Product editor that keeps ``(category, code)`` bound to a single row.

    The legacy BRcom system treated the code as the item's identity: retiring an
    item rewrote its row instead of deleting it, and reusing the code rewrote
    that same row back into service.  In ~20 years it never produced a retired
    row and a live row sharing one code.  This form restores that invariant —
    a code already in the collection is rejected, and a retired code is routed
    to the reuse path (``reusable_product``) instead of a second insert.
    """

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
        # Set by clean() when the requested code belongs to a retired row the
        # create view should revive rather than duplicate.
        self.reusable_product = None

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        code = cleaned_data.get('code')
        self.reusable_product = None
        if category is None or code is None:
            return cleaned_data

        taken = list(
            Product.objects.select_related('category')
            .filter(category=category, code=code)
            .exclude(pk=self.instance.pk or 0)
            .order_by('pk')
        )
        if not taken:
            return cleaned_data

        # An active holder is the blocking one; report it even when retired
        # rows share the code from before the codes were deduplicated.
        live = [product for product in taken if product.is_active]
        # Reuse rewrites the chosen row, so prefer one that never held a piece.
        # Picking merely the oldest would overwrite a retired garment's
        # description whenever a legacy shell happened to be created later,
        # destroying catalogue identity the operator never asked to discard.
        holder = live[0] if live else next(
            (product for product in taken if is_free_code_slot(product)),
            taken[0],
        )

        label = f'{category.prefix}{code}'
        if holder.is_active:
            raise ValidationError({'code': (
                f'O código {label} já está no acervo, usado por "{holder.description}". '
                'Retire esse item do acervo antes de reaproveitar o código.'
            )})
        if self.instance.pk:
            raise ValidationError({'code': (
                f'O código {label} pertence a um item anulado. Para reaproveitá-lo, '
                'cadastre um produto novo com esse código.'
            )})

        self.reusable_product = holder
        return cleaned_data


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
        input_formats=DATE_INPUT_FORMATS,
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
