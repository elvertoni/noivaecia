from django import forms
from django.core.exceptions import ValidationError

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
