from django.contrib import admin

from .models import Rental, RentalItem


class RentalItemInline(admin.TabularInline):
    model = RentalItem
    extra = 0


@admin.register(Rental)
class RentalAdmin(admin.ModelAdmin):
    list_display = ('number', 'customer', 'pickup_date', 'return_date', 'total_value', 'status')
    list_filter = ('status',)
    search_fields = ('number', 'customer__name')
    inlines = (RentalItemInline,)
