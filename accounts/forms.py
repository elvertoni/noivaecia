from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)

from core.ui import INPUT_CLASS
from core.modules import MODULES

from .models import ModulePermission, User

PASSWORD_CONFIRM_HELP_TEXT = 'Repita a senha para confirmar.'


class EmailUserCreationForm(UserCreationForm):
    """Signup form keyed on a unique email (RF-06)."""

    is_superuser = forms.BooleanField(
        label='Superusuário',
        required=False,
        help_text='Acesso irrestrito a todos os módulos e administração do sistema.',
    )

    module_permissions = forms.MultipleChoiceField(
        label='Módulos liberados',
        choices=MODULES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Marque apenas as áreas que este usuário poderá acessar.',
    )

    class Meta:
        model = User
        fields = ('email', 'first_name')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == 'module_permissions':
                continue
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
            'class': f'{INPUT_CLASS} pr-10',
            'autocomplete': 'new-password',
            'placeholder': 'Crie uma senha',
        })
        self.fields['password2'].label = 'Confirmar senha'
        self.fields['password2'].help_text = PASSWORD_CONFIRM_HELP_TEXT
        self.fields['password2'].widget.attrs.update({
            'class': f'{INPUT_CLASS} pr-10',
            'autocomplete': 'new-password',
            'placeholder': 'Repita a senha',
        })
        self.fields['module_permissions'].widget.attrs.update({
            'class': 'h-4 w-4 rounded border-slate-300 accent-brand-700 focus:ring-brand-300',
        })
        self.fields['is_superuser'].widget.attrs.update({
            'class': 'h-4 w-4 rounded border-slate-300 accent-brand-700 focus:ring-brand-300',
        })

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get('is_superuser'):
            user.is_superuser = True
            user.is_staff = True
        if commit:
            user.save()
        return user

    def save_module_permissions(self, user):
        selected = set(self.cleaned_data.get('module_permissions') or [])
        for key, _ in MODULES:
            ModulePermission.objects.update_or_create(
                user=user,
                module_key=key,
                defaults={'allowed': key in selected},
            )


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
        self.fields['password'].widget.attrs['class'] = f'{INPUT_CLASS} pr-10'
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
