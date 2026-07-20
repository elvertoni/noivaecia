from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class CustomerMessage(TimeStampedModel):
    """Record of a single WhatsApp message sent to a customer about one of
    their rentals (pickup reminder or return follow-up).

    One row per send attempt — including failed ones, so the reminder/return
    queues in ``notifications.services`` can tell an already-notified rental
    apart from one whose send failed and is still eligible for a retry.
    """

    class Kind(models.TextChoices):
        PICKUP_REMINDER = 'pickup_reminder', 'Aviso de retirada'
        RETURN_REMINDER = 'return_reminder', 'Cobrança de devolução'

    class Status(models.TextChoices):
        SENT = 'sent', 'Enviado'
        FAILED = 'failed', 'Falhou'

    rental = models.ForeignKey(
        'rentals.Rental',
        on_delete=models.PROTECT,
        related_name='customer_messages',
        verbose_name='locação',
    )
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        related_name='whatsapp_messages',
        verbose_name='cliente',
    )
    kind = models.CharField('tipo', max_length=20, choices=Kind.choices)
    phone = models.CharField('telefone', max_length=20)
    status = models.CharField('situação', max_length=10, choices=Status.choices)
    message_id = models.CharField('ID da mensagem', max_length=120, blank=True)
    error = models.TextField('erro', blank=True)
    sent_at = models.DateTimeField('enviado em', null=True, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='enviado por',
    )

    class Meta:
        verbose_name = 'mensagem ao cliente'
        verbose_name_plural = 'mensagens ao cliente'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('kind', 'status'), name='custmsg_kind_status_idx'),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} · {self.customer} · {self.get_status_display()}'
