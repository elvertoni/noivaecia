from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)

from core.ui import INPUT_CLASS

from .models import User

PASSWORD_CONFIRM_HELP_TEXT = 'Repita a senha para confirmar.'


class EmailUserCreationForm(UserCreationForm):
    """Signup form keyed on a unique email (RF-06)."""

    class Meta:
        model = User
        fields = ('email', 'first_name')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = INPUT_CLASS
        self.fields['email'].label = 'E-mail'
        self.fields['email'].widget.attrs.update({
            'autocomplete': 'email',
            'placeholder': 'seu@email.com',
        })
        self.fields['first_name'].label = 'Nome'
        self.fields['first_name'].widget.attrs.update({
            'autocomplete': 'given-name',
            'placeholder': 'Seu nome',
        })
        self.fields['password1'].label = 'Senha'
        self.fields['password1'].widget.attrs.update({
            'autocomplete': 'new-password',
            'placeholder': 'Crie uma senha',
        })
        self.fields['password2'].label = 'Confirmar senha'
        self.fields['password2'].help_text = PASSWORD_CONFIRM_HELP_TEXT
        self.fields['password2'].widget.attrs.update({
            'autocomplete': 'new-password',
            'placeholder': 'Repita a senha',
        })


class EmailAuthenticationForm(AuthenticationForm):
    """Login form that presents the username field as an email (RF-05)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'E-mail'
        self.fields['username'].widget = forms.EmailInput(
            attrs={
                'class': INPUT_CLASS,
                'autofocus': True,
                'autocomplete': 'username',
                'placeholder': 'seu@email.com',
            }
        )
        self.fields['password'].label = 'Senha'
        self.fields['password'].widget.attrs['class'] = INPUT_CLASS
        self.fields['password'].widget.attrs['autocomplete'] = 'current-password'
        self.fields['password'].widget.attrs['placeholder'] = 'Sua senha'


class EmailPasswordResetForm(PasswordResetForm):
    """Password reset form using the product input styles."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].label = 'E-mail cadastrado'
        self.fields['email'].widget = forms.EmailInput(
            attrs={
                'class': INPUT_CLASS,
                'autocomplete': 'email',
                'placeholder': 'seu@email.com',
            }
        )


class StyledSetPasswordForm(SetPasswordForm):
    """Password creation form with consistent labels and controls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_password1'].label = 'Nova senha'
        self.fields['new_password1'].widget.attrs.update({
            'class': INPUT_CLASS,
            'autocomplete': 'new-password',
            'placeholder': 'Digite a nova senha',
        })
        self.fields['new_password2'].label = 'Confirmar nova senha'
        self.fields['new_password2'].help_text = PASSWORD_CONFIRM_HELP_TEXT
        self.fields['new_password2'].widget.attrs.update({
            'class': INPUT_CLASS,
            'autocomplete': 'new-password',
            'placeholder': 'Repita a nova senha',
        })
