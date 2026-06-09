from decimal import Decimal

from django.db import models

from core.models import TimeStampedModel


class Receivable(TimeStampedModel):
    """One installment owed for a rental (RF-19)."""

    rental = models.ForeignKey(
        'rentals.Rental',
        on_delete=models.CASCADE,
        related_name='receivables',
        verbose_name='locação',
    )
    due_date = models.DateField('vencimento')
    amount = models.DecimalField('valor', max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField('valor pago', max_digits=10, decimal_places=2, default=0)
    balance = models.DecimalField('saldo', max_digits=10, decimal_places=2, default=0)
    last_payment_date = models.DateField('último pagamento', null=True, blank=True)

    class Meta:
        verbose_name = 'recebimento'
        verbose_name_plural = 'recebimentos'
        ordering = ('due_date',)

    def __str__(self):
        return f'Recebimento · Locação #{self.rental.number} · vence {self.due_date}'

    def save(self, *args, **kwargs):
        # Keep balance derived from amount and paid_amount.
        self.balance = (self.amount or Decimal('0')) - (self.paid_amount or Decimal('0'))
        super().save(*args, **kwargs)

    @property
    def is_paid(self):
        return self.balance <= 0

    def register_payment(self, value, payment_date):
        """Apply a payment, updating paid amount, balance and last payment date (RF-21)."""
        self.paid_amount = (self.paid_amount or Decimal('0')) + Decimal(value)
        self.last_payment_date = payment_date
        self.save()
        return self.balance
