from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Max, Q, Sum

from core.models import TimeStampedModel


class CashAccount(TimeStampedModel):
    """Named cash/bank account for financial movements (R3.04)."""

    name = models.CharField('nome', max_length=100)
    active = models.BooleanField('ativa', default=True)
    legacy_code = models.CharField('código legado', max_length=20, blank=True)

    class Meta:
        verbose_name = 'conta caixa'
        verbose_name_plural = 'contas caixa'
        ordering = ('name',)

    def __str__(self):
        return self.name


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
    # R3.01 — legacy migration metadata
    legacy_id = models.PositiveIntegerField('ID legado', null=True, blank=True, db_index=True)
    legacy_source = models.CharField('origem legada', max_length=50, blank=True)
    legacy_notes = models.TextField('notas de importação', blank=True)

    class Meta:
        verbose_name = 'recebimento'
        verbose_name_plural = 'recebimentos'
        ordering = ('due_date',)
        indexes = [
            models.Index(fields=('due_date',), name='rcv_due_date_idx'),
            models.Index(fields=('balance',), name='rcv_balance_idx'),
            models.Index(fields=('due_date', 'balance'), name='rcv_overdue_idx'),
            models.Index(fields=('balance', 'due_date'), name='rcv_balance_due_idx'),
            models.Index(fields=('rental', 'due_date'), name='rcv_rental_due_idx'),
            models.Index(
                fields=('due_date',),
                condition=Q(balance__gt=0),
                name='rcv_open_due_idx',
            ),
        ]

    def __str__(self):
        return f'Recebimento · Locação #{self.rental.number} · vence {self.due_date}'

    def save(self, *args, **kwargs):
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

    def recalculate_from_payments(self, save=True):
        """Recalculate paid_amount and balance by summing Payment records (R3.06)."""
        totals = self.payments.aggregate(
            total=Sum('amount'),
            last_date=Max(
                'payment_date',
                filter=Q(is_reversal=False, reversed_by__isnull=True),
            ),
        )
        total = totals['total'] or Decimal('0')
        self.paid_amount = total
        self.balance = (self.amount or Decimal('0')) - self.paid_amount
        self.last_payment_date = totals['last_date']
        if save:
            self.save(update_fields=['paid_amount', 'balance', 'last_payment_date', 'updated_at'])
        return self.balance


class Payment(TimeStampedModel):
    """Individual payment event applied to a receivable (R3.03)."""

    class Method(models.TextChoices):
        CASH = 'cash', 'Dinheiro'
        PIX = 'pix', 'Pix'
        CARD_DEBIT = 'card_debit', 'Débito'
        CARD_CREDIT = 'card_credit', 'Crédito'
        TRANSFER = 'transfer', 'Transferência'
        OTHER = 'other', 'Outro'

    receivable = models.ForeignKey(
        Receivable,
        on_delete=models.CASCADE,
        related_name='payments',
        verbose_name='recebimento',
    )
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='payments',
        verbose_name='cliente',
    )
    rental = models.ForeignKey(
        'rentals.Rental',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments',
        verbose_name='locação',
    )
    payment_date = models.DateField('data do pagamento')
    amount = models.DecimalField('valor pago', max_digits=10, decimal_places=2)
    interest_amount = models.DecimalField('juros', max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField('desconto', max_digits=10, decimal_places=2, default=0)
    method = models.CharField(
        'forma de pagamento', max_length=20, choices=Method.choices, default=Method.CASH
    )
    notes = models.TextField('observações', blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments_registered',
        verbose_name='operador',
    )
    # link back to Access movimento when imported
    legacy_movement_id = models.PositiveIntegerField(
        'ID do movimento legado', null=True, blank=True, db_index=True
    )
    is_reversal = models.BooleanField('é estorno', default=False)
    reversed_by = models.OneToOneField(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reversal_of',
        verbose_name='estornado por',
    )

    class Meta:
        verbose_name = 'pagamento'
        verbose_name_plural = 'pagamentos'
        ordering = ('payment_date', 'created_at')
        indexes = [
            models.Index(fields=('payment_date',), name='pmt_date_idx'),
            models.Index(fields=('customer', 'payment_date'), name='pmt_customer_date_idx'),
            models.Index(
                fields=('is_reversal', '-payment_date', '-created_at'),
                name='pmt_reversal_date_idx',
            ),
        ]

    def __str__(self):
        return f'Pagamento R${self.amount} · {self.payment_date}'


class FinancialMovement(TimeStampedModel):
    """Cash account movement — generated by payments or recorded manually (R3.05)."""

    class Direction(models.TextChoices):
        INFLOW = 'inflow', 'Entrada'
        OUTFLOW = 'outflow', 'Saída'

    class Source(models.TextChoices):
        PAYMENT = 'payment', 'Pagamento'
        MANUAL = 'manual', 'Manual'
        IMPORT = 'import', 'Importação'
        ADJUSTMENT = 'adjustment', 'Ajuste'
        REVERSAL = 'reversal', 'Estorno'

    date = models.DateField('data')
    account = models.ForeignKey(
        CashAccount,
        on_delete=models.PROTECT,
        related_name='movements',
        verbose_name='conta',
    )
    direction = models.CharField(
        'direção', max_length=10, choices=Direction.choices
    )
    amount = models.DecimalField('valor', max_digits=10, decimal_places=2)
    description = models.TextField('histórico', blank=True)
    source = models.CharField(
        'origem', max_length=20, choices=Source.choices, default=Source.MANUAL
    )
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_movements',
        verbose_name='cliente',
    )
    receivable = models.ForeignKey(
        Receivable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements',
        verbose_name='recebimento',
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_movements',
        verbose_name='pagamento',
    )
    rental = models.ForeignKey(
        'rentals.Rental',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_movements',
        verbose_name='locação',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_movements_created',
        verbose_name='criado por',
    )
    # legacy link to Access movimento.id
    legacy_id = models.PositiveIntegerField(
        'ID legado', null=True, blank=True, db_index=True
    )

    class Meta:
        verbose_name = 'movimento financeiro'
        verbose_name_plural = 'movimentos financeiros'
        ordering = ('-date', '-created_at')
        indexes = [
            models.Index(fields=('date',), name='fmv_date_idx'),
            models.Index(fields=('direction',), name='fmv_direction_idx'),
            models.Index(fields=('source',), name='fmv_source_idx'),
            models.Index(fields=('-date', '-created_at'), name='fmv_date_created_idx'),
            models.Index(fields=('direction', 'date'), name='fmv_direction_date_idx'),
            models.Index(
                fields=('source', 'direction', 'date'),
                name='fmv_source_direction_date_idx',
            ),
        ]

    def __str__(self):
        return f'{self.get_direction_display()} R${self.amount} · {self.date}'
