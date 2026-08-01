import re

from django import forms

from core.management.commands.normalize_cities import normalize as normalize_city
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


def _format_numeric_rg(value):
    """Format the two numeric RG lengths used by the existing customer base.

    Other lengths and alphanumeric identifiers remain untouched because RG
    formats vary by issuing authority.
    """
    if len(value) == 8:
        return f'{value[0]}.{value[1:4]}.{value[4:7]}-{value[7]}'
    if len(value) == 9:
        return f'{value[:2]}.{value[2:5]}.{value[5:8]}-{value[8]}'
    return value


class CustomerForm(forms.ModelForm):
    state = forms.ChoiceField(
        label='Estado',
        choices=ESTADOS_BR,
        required=False,
        widget=forms.Select(),
    )
    # A text input keeps city entry usable when JavaScript is unavailable. The
    # template adds a state-aware datalist when JavaScript is available.
    city = forms.CharField(
        label='Cidade',
        required=False,
        widget=forms.TextInput(),
    )

    class Meta:
        model = Customer
        fields = (
            'name', 'address', 'district', 'state', 'city',
            'rg', 'cpf', 'phone_mobile', 'phone_home',
            'alternate_phone_contact', 'phone_work', 'notes',
        )
        help_texts = {
            'rg': 'Digite como consta no documento; letras e pontuação serão preservadas.',
            'alternate_phone_contact': (
                'Informe de quem é o número ou a relação com o cliente.'
            ),
        }

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
            'autocomplete': 'name',
        })
        self.fields['address'].widget.attrs.update({
            'placeholder': 'Ex.: Rua das Flores, 123, Ap. 5',
            'autocomplete': 'street-address',
        })
        self.fields['district'].widget.attrs.update({
            'placeholder': 'Ex.: Centro',
            'autocomplete': 'address-level3',
        })
        self.fields['state'].widget.attrs['autocomplete'] = 'address-level1'
        self.fields['city'].widget.attrs.update({
            'autocomplete': 'address-level2',
            'list': 'customer-city-options',
        })
        self.fields['rg'].widget.attrs.update({
            'placeholder': '0.000.000-0',
            'maxlength': '20',
            'inputmode': 'text',
            'autocomplete': 'off',
            'autocapitalize': 'characters',
            'data-rg-format': 'true',
            'spellcheck': 'false',
        })
        self.fields['cpf'].widget.attrs.update({
            'placeholder': 'Ex.: 000.000.000-00',
            'maxlength': '14',
            'inputmode': 'numeric',
            'autocomplete': 'off',
            'data-mask': 'cpf',
        })
        self.fields['phone_home'].widget.attrs.update({
            'placeholder': 'Ex.: (43) 99999-1234',
            'maxlength': '20',
            'inputmode': 'tel',
            'autocomplete': 'off',
            'data-mask': 'phone',
        })
        self.fields['alternate_phone_contact'].widget.attrs.update({
            'placeholder': 'Ex.: esposo João, mãe ou vizinha Ana',
            'autocomplete': 'off',
        })
        self.fields['phone_mobile'].widget.attrs.update({
            'placeholder': 'Ex.: (43) 99123-4567',
            'maxlength': '20',
            'inputmode': 'tel',
            'autocomplete': 'tel',
            'data-mask': 'phone',
        })
        self.fields['phone_work'].widget.attrs.update({
            'placeholder': 'Ex.: (43) 3542-5678',
            'maxlength': '20',
            'inputmode': 'tel',
            'autocomplete': 'tel',
            'data-mask': 'phone',
        })
        self.fields['notes'].widget.attrs['placeholder'] = 'Observações adicionais...'

    def clean_cpf(self):
        cpf = self.cleaned_data.get('cpf', '').strip()
        if not cpf:
            return cpf
        if not re.fullmatch(r'[\d.\-\s]+', cpf):
            raise forms.ValidationError('CPF inválido. Use apenas números e a pontuação do CPF.')
        d = _digits(cpf)
        if not _validate_cpf(d):
            raise forms.ValidationError('CPF inválido. Verifique os dígitos informados.')
        return _format_cpf(d)

    def clean_city(self):
        city = self.cleaned_data.get('city', '').strip()
        if not city:
            return city
        return normalize_city(city) or city

    def clean_rg(self):
        rg = self.cleaned_data.get('rg', '').strip()
        if not rg:
            return rg
        if not re.fullmatch(r'[A-Za-z0-9.\-/\s]+', rg):
            raise forms.ValidationError(
                'RG inválido. Use apenas letras, números, pontos, hífen, barra e espaços.'
            )
        identifier = re.sub(r'[^A-Za-z0-9]', '', rg)
        if len(identifier) < 5:
            raise forms.ValidationError(
                'RG inválido. Informe ao menos 5 letras ou números.'
            )
        if rg.isdigit():
            return _format_numeric_rg(rg)
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
        if not re.fullmatch(r'[\d\s().+\-]+', phone):
            raise forms.ValidationError('Telefone inválido. Use apenas números e símbolos de telefone.')
        d = _digits(phone)
        if d.startswith('55') and len(d) in (12, 13):
            d = d[2:]
        if len(d) < 10 or len(d) > 11:
            raise forms.ValidationError(
                'Telefone inválido. Informe DDD + número com 10 ou 11 dígitos.'
            )
        return _format_phone(d)
