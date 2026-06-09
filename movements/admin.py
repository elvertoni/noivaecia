from django.contrib import admin

from .models import Pickup, Return


@admin.register(Pickup)
class PickupAdmin(admin.ModelAdmin):
    list_display = ('rental', 'pickup_date')
    search_fields = ('rental__number',)


@admin.register(Return)
class ReturnAdmin(admin.ModelAdmin):
    list_display = ('rental', 'return_date', 'days_late', 'penalty_applied')
    search_fields = ('rental__number',)
