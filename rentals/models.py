from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.urls import reverse

from core.models import TimeStampedModel


class Rental(TimeStampedModel):
    """Rental contract for a customer (RF-15)."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendente'
        PICKED_UP = 'picked_up', 'Retirado'
        RETURNED = 'returned', 'Devolvido'
        CANCELLED = 'cancelled', 'Cancelado'

    number = models.PositiveIntegerField('número', unique=True)
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        related_name='rentals',
        verbose_name='cliente',
    )
    pickup_date = models.DateField('data de retirada', db_index=True)
    return_date = models.DateField('data de retorno', db_index=True)
    total_value = models.DecimalField('valor total', max_digits=10, decimal_places=2, default=0)
    penalty_value = models.DecimalField('multa', max_digits=10, decimal_places=2, default=0)
    notes = models.TextField('observações', blank=True)
    status = models.CharField(
        'situação', max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    # R3.08 — uso/evento da locação (legado: locado.usar)
    use_for = models.CharField('usar em', max_length=200, blank=True)
    # R3.09 — campos de cancelamento
    cancelled_reason = models.TextField('motivo do cancelamento', blank=True)
    cancelled_at = models.DateTimeField('cancelado em', null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cancelled_rentals',
        verbose_name='cancelado por',
    )
    # R7.08 — contract audit trail
    contract_version = models.CharField('versão do contrato', max_length=50, blank=True)
    contract_printed_at = models.DateTimeField('contrato impresso em', null=True, blank=True)
    # R3.01 — metadados legados
    legacy_notes = models.TextField('notas de importação', blank=True)

    class Meta:
        verbose_name = 'locação'
        verbose_name_plural = 'locações'
        ordering = ('-number',)
        indexes = [
            models.Index(fields=('customer', 'status'), name='rental_customer_status_idx'),
            models.Index(fields=('status', 'pickup_date', 'number'), name='rental_status_pickup_num_idx'),
            models.Index(fields=('status', 'return_date', 'number'), name='rental_status_return_num_idx'),
            models.Index(fields=('customer', 'pickup_date'), name='rental_customer_pickup_idx'),
        ]

    def __str__(self):
        return f'Locação #{self.number}'

    def get_absolute_url(self):
        return reverse('rentals:detail', args=[self.pk])

    def recalculate_total(self, save=True):
        """Sum item values into ``total_value`` (RF-15 / 6.2.3)."""
        total = self.items.aggregate(total=Sum('value'))['total'] or 0
        self.total_value = total
        if save:
            self.save(update_fields=['total_value', 'updated_at'])
        return total


class RentalItem(TimeStampedModel):
    """Single line item within a rental (RF-16)."""

    rental = models.ForeignKey(
        Rental,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='locação',
    )
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.PROTECT,
        related_name='rental_items',
        verbose_name='produto',
    )
    description = models.CharField('descrição', max_length=200, blank=True)
    value = models.DecimalField('valor', max_digits=10, decimal_places=2, default=0)
    proof_photo = models.FileField(
        'foto de comprovação',
        upload_to='rentals/proof_photos/%Y/%m/',
        blank=True,
    )
    proof_photo_content_type = models.CharField(
        'tipo da foto',
        max_length=50,
        blank=True,
    )
    proof_photo_filename = models.CharField(
        'nome da foto',
        max_length=150,
        blank=True,
    )
    proof_photo_size = models.PositiveIntegerField('tamanho da foto', default=0)
    proof_photo_width = models.PositiveIntegerField('largura da foto', default=0)
    proof_photo_height = models.PositiveIntegerField('altura da foto', default=0)

    class Meta:
        verbose_name = 'item da locação'
        verbose_name_plural = 'itens da locação'

    def __str__(self):
        return f'{self.product} · {self.value}'

    @property
    def has_proof_photo(self):
        return self.proof_photo_size > 0
