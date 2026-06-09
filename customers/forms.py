from django import forms

from core.ui import INPUT_CLASS

from .models import Customer


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = (
            'name', 'address', 'district', 'city', 'rg', 'cpf',
            'phone_home', 'phone_mobile', 'phone_work', 'notes',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault('rows', 3)
            widget.attrs['class'] = INPUT_CLASS
