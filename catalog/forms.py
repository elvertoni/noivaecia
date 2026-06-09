from django import forms

from core.ui import INPUT_CLASS

from .models import Category, Product


def _style_fields(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.Textarea):
            field.widget.attrs.setdefault('rows', 3)
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
        _style_fields(self)
