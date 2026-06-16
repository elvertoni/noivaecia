import re
import unicodedata

from django.db import models
from django.urls import reverse

from core.models import TimeStampedModel


def _digits_only(value):
    return re.sub(r'\D', '', value or '')


def _normalize_name(value):
    if not value:
        return ''
    nfkd = unicodedata.normalize('NFKD', value)
    stripped = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ' '.join(stripped.lower().split())


class Customer(TimeStampedModel):
    """Store customer record (RF-11)."""

    name = models.CharField('nome', max_length=150)
    address = models.CharField('endereço', max_length=200, blank=True)
    district = models.CharField('bairro', max_length=100, blank=True)
    state = models.CharField('UF', max_length=2, blank=True, default='PR')
    city = models.CharField('cidade', max_length=100, blank=True, default='Bandeirantes')
    rg = models.CharField('RG', max_length=20, blank=True)
    cpf = models.CharField('CPF', max_length=14, blank=True)
    phone_home = models.CharField('telefone residencial', max_length=20, blank=True)
    phone_mobile = models.CharField('celular', max_length=20, blank=True)
    phone_work = models.CharField('telefone comercial', max_length=20, blank=True)
    cpf_digits = models.CharField('CPF (só dígitos)', max_length=14, blank=True, db_index=True)
    rg_digits = models.CharField('RG (só dígitos)', max_length=20, blank=True, db_index=True)
    phone_home_digits = models.CharField('tel. residencial (só dígitos)', max_length=20, blank=True)
    phone_mobile_digits = models.CharField('celular (só dígitos)', max_length=20, blank=True, db_index=True)
    phone_work_digits = models.CharField('tel. comercial (só dígitos)', max_length=20, blank=True)
    name_search = models.CharField('nome normalizado', max_length=180, blank=True)
    notes = models.TextField('observações', blank=True)
    # R3.01 / R3.02 — legacy migration metadata
    legacy_id = models.PositiveIntegerField('ID legado', null=True, blank=True, db_index=True)
    legacy_source = models.CharField('origem legada', max_length=50, blank=True)
    legacy_notes = models.TextField('notas de importação', blank=True)
    is_placeholder = models.BooleanField('é placeholder', default=False, db_index=True)
    is_active = models.BooleanField('ativo', default=True, db_index=True)

    class Meta:
        verbose_name = 'cliente'
        verbose_name_plural = 'clientes'
        ordering = ('name',)
        indexes = [
            models.Index(fields=('name',), name='customer_name_idx'),
            models.Index(fields=('cpf_digits',), name='customer_cpf_digits_idx'),
            models.Index(fields=('rg_digits',), name='customer_rg_digits_idx'),
            models.Index(fields=('phone_mobile_digits',), name='customer_mobile_digits_idx'),
        ]

    def save(self, *args, **kwargs):
        self.cpf_digits = _digits_only(self.cpf)
        self.rg_digits = _digits_only(self.rg)
        self.phone_home_digits = _digits_only(self.phone_home)
        self.phone_mobile_digits = _digits_only(self.phone_mobile)
        self.phone_work_digits = _digits_only(self.phone_work)
        self.name_search = _normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('customers:update', args=[self.pk])
