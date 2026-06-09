from django.contrib import admin

from .models import Company


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'cnpj', 'city', 'last_rental_number', 'daily_interest_rate')

    def has_add_permission(self, request):
        # Singleton: block adding a second row when one exists.
        return not Company.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
