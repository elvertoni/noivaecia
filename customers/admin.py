from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'cpf', 'phone_mobile')
    search_fields = ('name', 'cpf')
    list_filter = ('city',)
