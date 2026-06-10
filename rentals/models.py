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

    number = models.PositiveIntegerField('número', unique=True)
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        related_name='rentals',
        verbose_name='cliente',
    )
    pickup_date = models.DateField('data de retirada')
    return_date = models.DateField('data de retorno')
    total_value = models.DecimalField('valor total', max_digits=10, decimal_places=2, default=0)
    penalty_value = models.DecimalField('multa', max_digits=10, decimal_places=2, default=0)
    notes = models.TextField('observações', blank=True)
    status = models.CharField(
        'situação', max_length=20, choices=Status.choices, default=Status.PENDING
    )

    class Meta:
        verbose_name = 'locação'
        verbose_name_plural = 'locações'
        ordering = ('-number',)

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
    proof_photo = models.BinaryField(
        'foto de comprovação',
        blank=True,
        null=True,
        editable=False,
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
