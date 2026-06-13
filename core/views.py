from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.views.generic import TemplateView

from billing.models import Receivable
from core.modules import MODULES
from rentals.models import Rental

MODULE_URL_NAMES = {
    'customers': 'customers:list',
    'catalog': 'catalog:product_list',
    'company': 'company:edit',
    'rentals': 'rentals:list',
    'movements': 'rentals:list',
    'billing': 'rentals:list',
    'reports': 'reports:index',
    'maintenance': 'maintenance:index',
}


class DashboardView(LoginRequiredMixin, TemplateView):
    """Authenticated dashboard with module shortcuts and summary indicators (RF-10)."""

    template_name = 'core/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['modules'] = [
            {
                'key': key,
                'label': label,
                'url': reverse(MODULE_URL_NAMES.get(key, 'dashboard')),
            }
            for key, label in MODULES
            if self.request.user.has_module(key)
        ]

        to_pick_up = Rental.objects.filter(status=Rental.Status.PENDING).count()
        to_return = Rental.objects.filter(status=Rental.Status.PICKED_UP).count()
        open_receivables = Receivable.objects.filter(balance__gt=0).count()

        context['indicators'] = [
            {'label': 'Locações a retirar', 'value': to_pick_up},
            {'label': 'Locações a devolver', 'value': to_return},
            {'label': 'Recebimentos em aberto', 'value': open_receivables},
        ]
        return context
