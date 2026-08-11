from django.contrib import admin

from .models import (
    CashAccount, FinancialMovement, Payment, Receipt, ReceiptAllocation, Receivable,
)


@admin.register(Receivable)
class ReceivableAdmin(admin.ModelAdmin):
    list_display = ('rental', 'due_date', 'amount', 'paid_amount', 'balance', 'last_payment_date')
    list_filter = ('due_date',)
    search_fields = ('rental__number',)


@admin.register(CashAccount)
class CashAccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'active', 'legacy_code')
    list_filter = ('active',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        'receivable', 'payment_date', 'amount', 'method', 'is_reversal', 'user',
        'origin_receipt',
    )
    list_filter = ('payment_date', 'method', 'is_reversal')
    search_fields = ('receivable__rental__number',)
    raw_id_fields = ('receivable', 'customer', 'rental', 'user')

    @admin.display(description='recibo de origem')
    def origin_receipt(self, obj):
        allocation = getattr(obj, 'receipt_allocation', None)
        return allocation.receipt if allocation else '—'


@admin.register(FinancialMovement)
class FinancialMovementAdmin(admin.ModelAdmin):
    list_display = ('date', 'account', 'direction', 'amount', 'source', 'customer', 'payment')
    list_filter = ('date', 'direction', 'source', 'account')
    search_fields = ('description',)
    raw_id_fields = ('customer', 'receivable', 'payment', 'rental', 'created_by')


class ReceiptAllocationInline(admin.TabularInline):
    """Allocations only make sense read alongside the Receipt they belong to."""

    model = ReceiptAllocation
    extra = 0
    can_delete = False
    raw_id_fields = ('receivable', 'payment')
    fields = (
        'receivable', 'payment', 'cash_amount', 'principal_amount',
        'interest_amount', 'discount_amount',
    )

    def has_add_permission(self, request, obj=None):
        # Allocations are computed and written by the receipt service
        # (billing/services.py) alongside the Payment/FinancialMovement they
        # settle; a hand-added row here would have no matching cash movement.
        return False


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    """A Receipt is one real cash event — an auditable financial ledger row.

    Add/delete are disabled here: receipts are created by the billing
    receipt service, which stamps ``idempotency_key``/``payload_hash`` and
    the linked ``FinancialMovement`` together; a receipt created or deleted
    through the admin outside that flow would desync from the cash ledger
    it exists to record (and ``financial_movement``/``reversal_of`` are
    PROTECT, so a bare admin delete would fail anyway once allocations
    exist).
    """

    list_display = ('received_on', 'kind', 'customer', 'amount', 'method', 'operator')
    list_filter = ('kind', 'method')
    search_fields = ('customer__name', 'customer__cpf_digits')
    raw_id_fields = ('financial_movement', 'reversal_of', 'customer')
    readonly_fields = ('idempotency_key', 'payload_hash')
    inlines = [ReceiptAllocationInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
