from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from core.ui import INPUT_CLASS

from .models import User


class EmailUserCreationForm(UserCreationForm):
    """Signup form keyed on a unique email (RF-06)."""

    class Meta:
        model = User
        fields = ('email', 'first_name')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = INPUT_CLASS


class EmailAuthenticationForm(AuthenticationForm):
    """Login form that presents the username field as an email (RF-05)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'E-mail'
        self.fields['username'].widget = forms.EmailInput(
            attrs={'class': INPUT_CLASS, 'autofocus': True}
        )
        self.fields['password'].widget.attrs['class'] = INPUT_CLASS
