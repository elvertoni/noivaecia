from datetime import date as date_cls

from django.views.generic import TemplateView

from core.mixins import ModuleAccessMixin
from rentals.models import Rental

REPORT_TYPES = [
    ('a_retirar', 'A Retirar'),
    ('retirados', 'Retirados'),
    ('devolvidos', 'Devolvidos'),
    ('nao_devolvidos', 'Não Devolvidos'),
    ('historico', 'Histórico por prefixo'),
]


def _parse_date(value):
    try:
        return date_cls.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class ReportView(ModuleAccessMixin, TemplateView):
    """Tracking reports by type with date/prefix filters (RF-23, RF-24)."""

    module_key = 'reports'
    template_name = 'reports/report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        report_type = self.request.GET.get('type', '')
        date_start = _parse_date(self.request.GET.get('date_start', ''))
        date_end = _parse_date(self.request.GET.get('date_end', ''))
        prefix = self.request.GET.get('prefix', '').strip()
        code = self.request.GET.get('code', '').strip()

        context['report_types'] = REPORT_TYPES
        context['selected_type'] = report_type
        context['date_start'] = self.request.GET.get('date_start', '')
        context['date_end'] = self.request.GET.get('date_end', '')
        context['prefix'] = prefix
        context['code'] = code

        if not report_type:
            return context

        rentals = Rental.objects.select_related('customer')
        today = date_cls.today()

        if report_type == 'a_retirar':
            rentals = rentals.filter(status=Rental.Status.PENDING)
        elif report_type == 'retirados':
            rentals = rentals.filter(status=Rental.Status.PICKED_UP)
        elif report_type == 'devolvidos':
            rentals = rentals.filter(status=Rental.Status.RETURNED)
        elif report_type == 'nao_devolvidos':
            rentals = rentals.filter(
                status=Rental.Status.PICKED_UP, return_date__lt=today
            )
        # 'historico' keeps all rentals, narrowed by the filters below.

        if date_start:
            rentals = rentals.filter(pickup_date__gte=date_start)
        if date_end:
            rentals = rentals.filter(pickup_date__lte=date_end)
        if prefix:
            rentals = rentals.filter(items__product__category__prefix__iexact=prefix)
        if code:
            rentals = rentals.filter(items__product__code=code)

        context['rentals'] = rentals.distinct().order_by('-number')
        context['has_results'] = True
        return context
