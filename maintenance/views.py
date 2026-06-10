from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView, View

from billing.models import Receivable
from core.mixins import ModuleAccessMixin
from customers.models import Customer
from rentals.models import Rental

from catalog.models import Category, Product


class MaintenanceAccessMixin(ModuleAccessMixin):
    module_key = 'maintenance'


class MaintenanceView(MaintenanceAccessMixin, TemplateView):
    """Restricted maintenance area with controlled DB routines (RF-25)."""

    template_name = 'maintenance/maintenance.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['stats'] = {
            'customers': Customer.objects.count(),
            'categories': Category.objects.count(),
            'products': Product.objects.count(),
            'rentals': Rental.objects.count(),
            'receivables': Receivable.objects.count(),
        }
        return context


class RecalculateRentalTotalsView(MaintenanceAccessMixin, View):
    """Recompute every rental's total from its items (controlled routine)."""

    def post(self, request, *args, **kwargs):
        count = 0
        for rental in Rental.objects.all():
            rental.recalculate_total()
            count += 1
        messages.success(request, f'Totais recalculados para {count} locação(ões).')
        return redirect('maintenance:index')


class RecalculateBalancesView(MaintenanceAccessMixin, View):
    """Re-save receivables to recompute their balance (controlled routine)."""

    def post(self, request, *args, **kwargs):
        count = 0
        for receivable in Receivable.objects.all():
            receivable.save()
            count += 1
        messages.success(request, f'Saldos recalculados para {count} recebimento(s).')
        return redirect('maintenance:index')
