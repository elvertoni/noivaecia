from datetime import time

from django.db import models, transaction

from core.models import TimeStampedModel


class Company(TimeStampedModel):
    """Singleton store configuration (RF-14).

    Only one row ever exists (pk=1). Use ``Company.load()`` to fetch/create it
    and ``next_rental_number()`` to reserve the next sequential rental number.
    """

    name = models.CharField('nome', max_length=150, blank=True)
    address = models.CharField('endereço', max_length=200, blank=True)
    city = models.CharField('cidade', max_length=100, blank=True)
    cnpj = models.CharField('CNPJ', max_length=18, blank=True)
    phones = models.CharField('telefones', max_length=150, blank=True)
    last_rental_number = models.PositiveIntegerField('última locação', default=0)
    daily_interest_rate = models.DecimalField(
        'juros ao dia (%)', max_digits=5, decimal_places=2, default=0
    )
    # R6.08 — separate financial rules (RF-FI-09)
    late_fee_rate = models.DecimalField(
        'multa moratória (%)', max_digits=5, decimal_places=2, default=2,
        help_text='Percentual de multa moratória aplicado sobre o valor do título.',
    )
    monthly_interest_rate = models.DecimalField(
        'juros ao mês (%)', max_digits=5, decimal_places=2, default=1,
        help_text='Juros de mora mensais; dividido por 30 para cálculo diário.',
    )
    damage_penalty_rate = models.DecimalField(
        'penalidade por dano (%)', max_digits=5, decimal_places=2, default=50,
        help_text='Percentual do valor do item cobrado em caso de dano.',
    )
    loss_penalty_rate = models.DecimalField(
        'penalidade por perda/não devolução (%)', max_digits=5, decimal_places=2, default=100,
        help_text='Percentual do valor do item cobrado em caso de perda ou não devolução.',
    )
    footer_message = models.CharField('mensagem de rodapé', max_length=255, blank=True)

    # WhatsApp daily report (RF-notifications)
    whatsapp_reports_enabled = models.BooleanField(
        'enviar relatório diário por WhatsApp', default=False,
    )
    whatsapp_report_number = models.TextField(
        'números do WhatsApp (com DDI, ex: 5543999998888)', blank=True,
        help_text='Informe um ou mais números separados por vírgula, espaço ou linha.',
    )
    whatsapp_report_time = models.TimeField(
        'horário do envio diário', default=time(7, 30),
    )

    class Meta:
        verbose_name = 'empresa'
        verbose_name_plural = 'empresa'

    def __str__(self):
        return self.name or 'Configuração da empresa'

    def save(self, *args, **kwargs):
        # Force a single row. When no row exists yet, force an INSERT so the
        # auto_now_add timestamp is populated (a forced pk would otherwise send
        # a fresh instance down the UPDATE path and skip created_at).
        self.pk = 1
        existing = type(self).objects.filter(pk=1).values('created_at').first()
        if existing is None:
            kwargs['force_insert'] = True
            kwargs.pop('force_update', None)
        elif self.created_at is None:
            # Fresh instance saved over the existing row: keep its created_at so
            # the UPDATE does not violate the NOT NULL constraint.
            self.created_at = existing['created_at']
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Return the singleton instance, creating it on first access."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def next_rental_number(cls):
        """Atomically increment and return the next sequential rental number."""
        with transaction.atomic():
            company = cls.objects.select_for_update().get_or_create(pk=1)[0]
            company.last_rental_number += 1
            company.save(update_fields=['last_rental_number', 'updated_at'])
            return company.last_rental_number
