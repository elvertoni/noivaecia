from django.contrib import admin

from .models import Receivable


@admin.register(Receivable)
class ReceivableAdmin(admin.ModelAdmin):
    list_display = ('rental', 'due_date', 'amount', 'paid_amount', 'balance', 'last_payment_date')
    list_filter = ('due_date',)
    search_fields = ('rental__number',)
