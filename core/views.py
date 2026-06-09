from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from billing.models import Receivable
from core.modules import MODULES
from rentals.models import Rental


class DashboardView(LoginRequiredMixin, TemplateView):
    """Authenticated dashboard with module shortcuts and summary indicators (RF-10)."""

    template_name = 'core/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['modules'] = [{'key': key, 'label': label} for key, label in MODULES]

        to_pick_up = Rental.objects.filter(status=Rental.Status.PENDING).count()
        to_return = Rental.objects.filter(status=Rental.Status.PICKED_UP).count()
        open_receivables = Receivable.objects.filter(balance__gt=0).count()

        context['indicators'] = [
            {'label': 'Locações a retirar', 'value': to_pick_up},
            {'label': 'Locações a devolver', 'value': to_return},
            {'label': 'Recebimentos em aberto', 'value': open_receivables},
        ]
        return context
