from django import forms

from core.ui import INPUT_CLASS

from .models import Rental, RentalItem


def _style(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.Textarea):
            field.widget.attrs.setdefault('rows', 3)
        css = field.widget.attrs.get('class', '')
        field.widget.attrs['class'] = (css + ' ' + INPUT_CLASS).strip()


class RentalForm(forms.ModelForm):
    """Rental header form. Number, total and status are managed by the view."""

    class Meta:
        model = Rental
        fields = ('customer', 'pickup_date', 'return_date', 'penalty_value', 'notes')
        widgets = {
            'pickup_date': forms.DateInput(attrs={'type': 'date'}),
            'return_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class RentalItemForm(forms.ModelForm):
    class Meta:
        model = RentalItem
        fields = ('product', 'description', 'value')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


RentalItemFormSet = forms.inlineformset_factory(
    Rental,
    RentalItem,
    form=RentalItemForm,
    extra=3,
    can_delete=True,
)
