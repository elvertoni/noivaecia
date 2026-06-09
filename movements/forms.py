from django import forms

from core.ui import INPUT_CLASS

from .models import Pickup, Return


class PickupForm(forms.ModelForm):
    class Meta:
        model = Pickup
        fields = ('pickup_date',)
        widgets = {'pickup_date': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pickup_date'].widget.attrs['class'] = INPUT_CLASS


class ReturnForm(forms.ModelForm):
    """Return form. days_late and penalty_applied are computed in the view."""

    class Meta:
        model = Return
        fields = ('return_date',)
        widgets = {'return_date': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['return_date'].widget.attrs['class'] = INPUT_CLASS
