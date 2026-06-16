from django.contrib import admin

from .models import CashAccount, FinancialMovement, Payment, Receivable


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
    list_display = ('receivable', 'payment_date', 'amount', 'method', 'is_reversal', 'user')
    list_filter = ('payment_date', 'method', 'is_reversal')
    search_fields = ('receivable__rental__number',)
    raw_id_fields = ('receivable', 'customer', 'rental', 'user')


@admin.register(FinancialMovement)
class FinancialMovementAdmin(admin.ModelAdmin):
    list_display = ('date', 'account', 'direction', 'amount', 'source', 'customer', 'payment')
    list_filter = ('date', 'direction', 'source', 'account')
    search_fields = ('description',)
    raw_id_fields = ('customer', 'receivable', 'payment', 'rental', 'created_by')
