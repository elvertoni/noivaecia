from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.urls import reverse

from core.models import TimeStampedModel

CASH_DISCOUNT_RATE = Decimal('0.10')


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
    # Named ``penalty_value`` for historical reasons: it maps to the legacy
    # ``locado.multa`` column. The 2026-08 migration showed what the legacy data
    # actually holds — a per-rental amount worth 1.2x to 3x the rental itself,
    # which the shop writes into clause 3 of the printed contract as the
    # replacement price of the garments. The label follows the data, the field
    # name stays put so the legacy importer and existing payloads keep working.
    penalty_value = models.DecimalField(
        'valor de reposição', max_digits=10, decimal_places=2, default=0,
        help_text='Quanto custa repor as peças desta locação, se forem perdidas ou '
                  'danificadas. Sai impresso na cláusula 3 do contrato.',
    )
    wearer_name = models.CharField(
        'quem vai usar', max_length=150, blank=True,
        help_text='Preencha quando quem vai usar a peça não é o(a) locatário(a) (ex.: esposa loca o terno para o marido usar).',
    )
    cash_discount = models.BooleanField('desconto à vista', default=False)
    cash_discount_percent = models.DecimalField(
        'desconto à vista (%)', max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Percentual customizado para esta locação. Em branco, usa o padrão de 10%.',
    )
    cash_discount_amount = models.DecimalField(
        'desconto à vista (R$)', max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Valor fixo em reais. Preencha apenas um dos dois campos de desconto.',
    )
    notes = models.TextField('observações', blank=True)
    status = models.CharField(
        'situação', max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
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

    @staticmethod
    def compute_cash_discount(total, *, applied, percent=None, amount=None):
        """Return ``(final_value, discount_amount)`` for a given total (R7.05).

        A custom fixed ``amount`` takes precedence over ``percent`` (an
        attendant may negotiate a bigger discount for a returning customer);
        with neither set, falls back to the standard 10% rate.
        """
        total = total or Decimal('0')
        if not applied:
            return total, Decimal('0.00')
        if amount is not None:
            discount = min(amount, total)
        else:
            rate = (percent if percent is not None else CASH_DISCOUNT_RATE * 100) / Decimal('100')
            discount = (total * rate).quantize(Decimal('0.01'))
        return (total - discount).quantize(Decimal('0.01')), discount

    @property
    def final_value(self):
        """``total_value`` net of the cash discount, when applied (R7.05).

        This is the amount actually owed by the customer — the one used to
        generate receivables/installments and shown on the printed contract.
        ``total_value`` itself stays the raw item sum (RF-15 contract).
        """
        final, _ = self.compute_cash_discount(
            self.total_value,
            applied=self.cash_discount,
            percent=self.cash_discount_percent,
            amount=self.cash_discount_amount,
        )
        return final

    @property
    def discount_amount(self):
        """Amount subtracted by the cash discount, or zero when not applied."""
        _, discount = self.compute_cash_discount(
            self.total_value,
            applied=self.cash_discount,
            percent=self.cash_discount_percent,
            amount=self.cash_discount_amount,
        )
        return discount


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
    product_prefix_snapshot = models.CharField(
        'prefixo da peça na locação',
        max_length=10,
        blank=True,
    )
    product_code_snapshot = models.PositiveIntegerField(
        'código da peça na locação',
        null=True,
        blank=True,
    )
    product_description_snapshot = models.CharField(
        'descrição da peça na locação',
        max_length=200,
        blank=True,
    )
    product_color_snapshot = models.CharField(
        'cor da peça na locação',
        max_length=50,
        blank=True,
    )
    product_size_snapshot = models.CharField(
        'tamanho da peça na locação',
        max_length=50,
        blank=True,
    )
    product_snapshot_captured = models.BooleanField(
        'dados da peça preservados',
        default=False,
    )
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
    wearer_name = models.CharField(
        'quem vai usar', max_length=150, blank=True,
        help_text='Preencha quando quem vai usar esta peça não é o(a) locatário(a) '
                  '(ex.: esposa loca o terno para o marido usar).',
    )

    class Meta:
        verbose_name = 'item da locação'
        verbose_name_plural = 'itens da locação'

    def __str__(self):
        description = self.display_description
        label = self.product_reference
        if description:
            label = f'{label} · {description}' if label else description
        return f'{label} · {self.value}'

    def _capture_product_snapshot(self):
        """Copy mutable catalogue data when this line starts pointing to a product."""
        product = self._state.fields_cache.get('product')
        if product is None or product.pk != self.product_id:
            product_model = self._meta.get_field('product').remote_field.model
            product = product_model.objects.select_related('category').get(pk=self.product_id)

        self.product_prefix_snapshot = product.category.prefix
        self.product_code_snapshot = product.code
        self.product_description_snapshot = product.description
        self.product_color_snapshot = product.color
        self.product_size_snapshot = product.size
        self.product_snapshot_captured = True

    def save(self, *args, **kwargs):
        """Refresh the snapshot only for a new line or a deliberate product swap."""
        update_fields = kwargs.get('update_fields')
        product_is_being_saved = (
            update_fields is None
            or 'product' in update_fields
            or 'product_id' in update_fields
        )
        capture_snapshot = False

        if self.product_id and product_is_being_saved:
            if self._state.adding:
                capture_snapshot = True
            else:
                previous = type(self).objects.filter(pk=self.pk).values(
                    'product_id',
                    'product_snapshot_captured',
                ).first()
                capture_snapshot = (
                    previous is None
                    or previous['product_id'] != self.product_id
                    or not previous['product_snapshot_captured']
                )

        if capture_snapshot:
            self._capture_product_snapshot()
            if update_fields is not None:
                kwargs['update_fields'] = set(update_fields) | {
                    'product_prefix_snapshot',
                    'product_code_snapshot',
                    'product_description_snapshot',
                    'product_color_snapshot',
                    'product_size_snapshot',
                    'product_snapshot_captured',
                }

        super().save(*args, **kwargs)

    @property
    def product_reference(self):
        if self.product_snapshot_captured:
            prefix = self.product_prefix_snapshot
            code = self.product_code_snapshot
        else:
            product = getattr(self, 'product', None)
            prefix = product.category.prefix if product else ''
            code = product.code if product else None
        return f'{prefix}{code}' if code is not None else prefix

    @property
    def display_description(self):
        if self.product_snapshot_captured:
            return self.product_description_snapshot
        product = getattr(self, 'product', None)
        return product.description if product else ''

    @property
    def display_color(self):
        if self.product_snapshot_captured:
            return self.product_color_snapshot
        product = getattr(self, 'product', None)
        return product.color if product else ''

    @property
    def display_size(self):
        if self.product_snapshot_captured:
            return self.product_size_snapshot
        product = getattr(self, 'product', None)
        return product.size if product else ''

    @property
    def has_proof_photo(self):
        return self.proof_photo_size > 0
