from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from billing.models import Receivable
from core.modules import MODULES
from rentals.models import Rental

MODULE_URL_NAMES = {
    'customers': 'customers:list',
    'catalog': 'catalog:product_list',
    'company': 'company:edit',
    'rentals': 'rentals:list',
    'movements': 'movements:pickup_list',
    'billing': 'billing:dashboard',
    'reports': 'reports:index',
    'maintenance': 'maintenance:index',
}


@require_GET
def healthz(request):
    return JsonResponse({'status': 'ok'})


class DashboardView(LoginRequiredMixin, TemplateView):
    """Authenticated dashboard with module shortcuts and summary indicators (RF-10)."""

    template_name = 'core/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed_module_keys = {
            key
            for key, _ in MODULES
            if self.request.user.has_module(key)
        }
        context['modules'] = [
            {
                'key': key,
                'label': label,
                'url': reverse(MODULE_URL_NAMES.get(key, 'dashboard')),
            }
            for key, label in MODULES
            if key in allowed_module_keys
        ]

        indicators = []
        if 'movements' in allowed_module_keys:
            self.add_today_summary(context)
            rental_counts = Rental.objects.aggregate(
                to_pick_up=Count(
                    'id',
                    filter=(
                        Q(status=Rental.Status.PENDING)
                        & ~Q(legacy_notes__contains=Rental.LEGACY_PAGAR_ONLY_MARKER)
                    ),
                ),
                to_return=Count(
                    'id',
                    filter=Q(status=Rental.Status.PICKED_UP),
                ),
            )
            indicators.extend([
                {
                    'label': 'Locações a retirar',
                    'value': rental_counts['to_pick_up'],
                    'url': reverse('movements:pickup_list'),
                },
                {
                    'label': 'Locações a devolver',
                    'value': rental_counts['to_return'],
                    'url': reverse('movements:return_list'),
                },
            ])
        if 'billing' in allowed_module_keys:
            indicators.append({
                'label': 'Recebimentos em aberto',
                'value': Receivable.objects.filter(balance__gt=0).count(),
                'url': reverse('billing:receivables'),
            })

        context['indicators'] = indicators
        return context

    TODAY_SUMMARY_LIMIT = 6

    def add_today_summary(self, context):
        """Today's pickups and returns for the dashboard "Resumo de hoje" panel.

        Built on ``Rental.pending_pickup_queryset()`` rather than repeating its
        exclusion here: that classmethod is the single definition of "a garment
        actually waiting to be picked up", and a copy would let this panel drift
        away from the movements screens the operator opens next.
        """
        today = timezone.localdate()
        pickups = (
            Rental.pending_pickup_queryset()
            .filter(pickup_date=today)
            .select_related('customer')
            .order_by('number')
        )
        returns = (
            Rental.objects.filter(status=Rental.Status.PICKED_UP, return_date=today)
            .select_related('customer')
            .order_by('number')
        )

        context['today'] = today
        context['today_pickups'] = pickups[: self.TODAY_SUMMARY_LIMIT]
        context['today_returns'] = returns[: self.TODAY_SUMMARY_LIMIT]
        context['today_pickups_total'] = pickups.count()
        context['today_returns_total'] = returns.count()
        context['today_summary_limit'] = self.TODAY_SUMMARY_LIMIT
