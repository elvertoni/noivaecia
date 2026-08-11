from django.db import models

from core.models import TimeStampedModel


class Pickup(TimeStampedModel):
    """Records that a rental's items were picked up on a date (RF-17)."""

    rental = models.OneToOneField(
        'rentals.Rental',
        on_delete=models.CASCADE,
        related_name='pickup',
        verbose_name='locação',
    )
    pickup_date = models.DateField('data de retirada', db_index=True)

    class Meta:
        verbose_name = 'retirada'
        verbose_name_plural = 'retiradas'

    def __str__(self):
        return f'Retirada · Locação #{self.rental.number}'


class Return(TimeStampedModel):
    """Records the return of a rental's items, with late days/penalty (RF-18)."""

    rental = models.OneToOneField(
        'rentals.Rental',
        on_delete=models.CASCADE,
        related_name='return_record',
        verbose_name='locação',
    )
    return_date = models.DateField('data de devolução', db_index=True)
    days_late = models.PositiveIntegerField('dias de atraso', default=0)
    penalty_applied = models.DecimalField(
        'multa aplicada', max_digits=10, decimal_places=2, default=0
    )
    damage_amount = models.DecimalField(
        'valor cobrado por danos',
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    # Named ``_legacy`` because the damage penalty used to be a percentage of
    # each item's value; kept exclusively as audit evidence for returns
    # recorded before the switch to a single flat R$ amount per rental.
    # ``damage_amount`` alone captures the new model (the multa is charged
    # once per rental when damage occurs, regardless of how many items are
    # marked as damaged — most rentals are single-item anyway).
    damage_rate_legacy = models.DecimalField(
        'percentual aplicado por dano (legado)',
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Auditoria: percentual usado antes da multa virar valor fixo em R$.',
    )
    damaged_items = models.ManyToManyField(
        'rentals.RentalItem',
        blank=True,
        related_name='damage_return_records',
        verbose_name='peças danificadas',
    )
    loss_amount = models.DecimalField(
        'valor cobrado por perda/não devolução',
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    loss_rate = models.DecimalField(
        'percentual aplicado por perda/não devolução',
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    lost_items = models.ManyToManyField(
        'rentals.RentalItem',
        blank=True,
        related_name='loss_return_records',
        verbose_name='peças não devolvidas',
    )
    damage_notes = models.TextField('observações sobre danos', blank=True)

    class Meta:
        verbose_name = 'devolução'
        verbose_name_plural = 'devoluções'

    def __str__(self):
        return f'Devolução · Locação #{self.rental.number}'
