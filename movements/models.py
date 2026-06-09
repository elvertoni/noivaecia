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
    pickup_date = models.DateField('data de retirada')

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
    return_date = models.DateField('data de devolução')
    days_late = models.PositiveIntegerField('dias de atraso', default=0)
    penalty_applied = models.DecimalField(
        'multa aplicada', max_digits=10, decimal_places=2, default=0
    )

    class Meta:
        verbose_name = 'devolução'
        verbose_name_plural = 'devoluções'

    def __str__(self):
        return f'Devolução · Locação #{self.rental.number}'
