from django.db import models
from django.urls import reverse

from core.models import TimeStampedModel


class Customer(TimeStampedModel):
    """Store customer record (RF-11)."""

    name = models.CharField('nome', max_length=150)
    address = models.CharField('endereço', max_length=200, blank=True)
    district = models.CharField('bairro', max_length=100, blank=True)
    city = models.CharField('cidade', max_length=100, blank=True)
    rg = models.CharField('RG', max_length=20, blank=True)
    cpf = models.CharField('CPF', max_length=14, blank=True)
    phone_home = models.CharField('telefone residencial', max_length=20, blank=True)
    phone_mobile = models.CharField('celular', max_length=20, blank=True)
    phone_work = models.CharField('telefone comercial', max_length=20, blank=True)
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

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('customers:update', args=[self.pk])
