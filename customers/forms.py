import re

from django import forms

from core.ui import INPUT_CLASS

from .models import Customer

ESTADOS_BR = [
    ('', 'Selecione o estado'),
    ('AC', 'Acre'),
    ('AL', 'Alagoas'),
    ('AP', 'Amapá'),
    ('AM', 'Amazonas'),
    ('BA', 'Bahia'),
    ('CE', 'Ceará'),
    ('DF', 'Distrito Federal'),
    ('ES', 'Espírito Santo'),
    ('GO', 'Goiás'),
    ('MA', 'Maranhão'),
    ('MT', 'Mato Grosso'),
    ('MS', 'Mato Grosso do Sul'),
    ('MG', 'Minas Gerais'),
    ('PA', 'Pará'),
    ('PB', 'Paraíba'),
    ('PR', 'Paraná'),
    ('PE', 'Pernambuco'),
    ('PI', 'Piauí'),
    ('RJ', 'Rio de Janeiro'),
    ('RN', 'Rio Grande do Norte'),
    ('RS', 'Rio Grande do Sul'),
    ('RO', 'Rondônia'),
    ('RR', 'Roraima'),
    ('SC', 'Santa Catarina'),
    ('SP', 'São Paulo'),
    ('SE', 'Sergipe'),
    ('TO', 'Tocantins'),
]


def _digits(value):
    return re.sub(r'\D', '', value or '')


def _validate_cpf(cpf):
    d = _digits(cpf)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for i, peso_inicial in enumerate([10, 11]):
        soma = sum(int(d[j]) * (peso_inicial - j) for j in range(9 + i))
        resto = (soma * 10 % 11) % 10
        if resto != int(d[9 + i]):
            return False
    return True


def _format_cpf(d):
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'


def _format_phone(d):
    if len(d) == 11:
        return f'({d[:2]}) {d[2:7]}-{d[7:]}'
    return f'({d[:2]}) {d[2:6]}-{d[6:]}'


class CustomerForm(forms.ModelForm):
    state = forms.ChoiceField(
        label='Estado',
        choices=ESTADOS_BR,
        required=False,
        widget=forms.Select(),
    )
    # CharField + Select widget: Django não valida as choices (JS as popula dinamicamente)
    city = forms.CharField(
        label='Cidade',
        required=False,
        widget=forms.Select(choices=[('', 'Selecione a cidade')]),
    )

    class Meta:
        model = Customer
        fields = (
            'name', 'address', 'district', 'state', 'city',
            'rg', 'cpf', 'phone_home', 'phone_mobile', 'phone_work', 'notes',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault('rows', 3)
            widget.attrs['class'] = INPUT_CLASS

        if not self.instance.pk:
            self.fields['state'].initial = 'PR'
            self.fields['city'].initial = 'Bandeirantes'

        self.fields['name'].widget.attrs.update({
            'placeholder': 'Ex.: Maria da Silva',
            'autofocus': True,
        })
        self.fields['address'].widget.attrs['placeholder'] = 'Ex.: Rua das Flores, 123, Ap. 5'
        self.fields['district'].widget.attrs['placeholder'] = 'Ex.: Centro'
        self.fields['city'].widget.attrs['id'] = 'id_city'
        self.fields['rg'].widget.attrs.update({
            'placeholder': 'Ex.: 12.345.678-9',
            'maxlength': '15',
            'data-mask': 'rg',
        })
        self.fields['cpf'].widget.attrs.update({
            'placeholder': 'Ex.: 000.000.000-00',
            'maxlength': '14',
            'data-mask': 'cpf',
        })
        self.fields['phone_home'].widget.attrs.update({
            'placeholder': 'Ex.: (43) 3542-1234',
            'maxlength': '15',
            'data-mask': 'phone',
        })
        self.fields['phone_mobile'].widget.attrs.update({
            'placeholder': 'Ex.: (43) 99123-4567',
            'maxlength': '16',
            'data-mask': 'phone',
        })
        self.fields['phone_work'].widget.attrs.update({
            'placeholder': 'Ex.: (43) 3542-5678',
            'maxlength': '15',
            'data-mask': 'phone',
        })
        self.fields['notes'].widget.attrs['placeholder'] = 'Observações adicionais...'

    def clean_cpf(self):
        cpf = self.cleaned_data.get('cpf', '').strip()
        if not cpf:
            return cpf
        d = _digits(cpf)
        if not _validate_cpf(d):
            raise forms.ValidationError('CPF inválido. Verifique os dígitos informados.')
        return _format_cpf(d)

    def clean_rg(self):
        rg = self.cleaned_data.get('rg', '').strip()
        if not rg:
            return rg
        d = _digits(rg)
        if len(d) < 5 or len(d) > 10:
            raise forms.ValidationError('RG inválido. Informe entre 5 e 10 dígitos.')
        return rg

    def clean_phone_home(self):
        return self._clean_phone(self.cleaned_data.get('phone_home', ''))

    def clean_phone_mobile(self):
        return self._clean_phone(self.cleaned_data.get('phone_mobile', ''))

    def clean_phone_work(self):
        return self._clean_phone(self.cleaned_data.get('phone_work', ''))

    def _clean_phone(self, value):
        phone = (value or '').strip()
        if not phone:
            return phone
        d = _digits(phone)
        if len(d) < 10 or len(d) > 11:
            raise forms.ValidationError(
                'Telefone inválido. Informe DDD + número com 10 ou 11 dígitos.'
            )
        return _format_phone(d)
