"""Reports module — isolated per-type services (R11.01)."""
import csv
from datetime import date as date_cls

from django.http import HttpResponse
from django.views.generic import TemplateView

from core.mixins import ModuleAccessMixin

from .services import (
    report_a_retirar,
    report_atrasados,
    report_contas_cliente,
    report_contas_vencimento,
    report_devolvidos,
    report_locacoes,
    report_retirados,
)


class ReportsAccessMixin(ModuleAccessMixin):
    module_key = 'reports'


class ReportsIndexView(ReportsAccessMixin, TemplateView):
    """Hub with links to all report types (R11.01)."""
    template_name = 'reports/index.html'


class _BaseReportView(ReportsAccessMixin, TemplateView):
    """Base with common filter param helpers and CSV export (R11.09/R11.10)."""
    csv_filename = 'relatorio.csv'

    def _p(self, key, default=''):
        return self.request.GET.get(key, default).strip()

    def _csv_response(self, rows, headers):
        """Build CSV HttpResponse with UTF-8 BOM for Excel (R11.10)."""
        from django.core.exceptions import PermissionDenied
        if not self.request.user.has_action('reports.export'):
            raise PermissionDenied
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="{self.csv_filename}"'
        writer = csv.writer(response, delimiter=';')
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        return response


class ARetirarReportView(_BaseReportView):
    """Produtos a retirar — equiv. locados.rpt não retirados (R11.02)."""
    template_name = 'reports/a_retirar.html'
    csv_filename = 'a_retirar.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self):
        return report_a_retirar(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), prefix=self._p('prefix'), code=self._p('code'),
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Uso/Evento', 'Retirada', 'Retorno previsto', 'Total']
        rows = []
        for r in self._get_data():
            rows.append([
                f'#{r.number}', r.customer.name, r.use_for or '',
                r.pickup_date.strftime('%d/%m/%Y'), r.return_date.strftime('%d/%m/%Y'),
                str(r.total_value),
            ])
        return self._csv_response(rows, headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'rentals': self._get_data(),
            'date_from': self._p('date_from'),
            'date_to': self._p('date_to'),
            'customer': self._p('customer'),
            'prefix': self._p('prefix'),
            'code': self._p('code'),
            'today': date_cls.today(),
        })
        return ctx


class RetiradosReportView(_BaseReportView):
    """Produtos retirados — equiv. locados12.rpt (R11.03)."""
    template_name = 'reports/retirados.html'
    csv_filename = 'retirados.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self):
        return report_retirados(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), prefix=self._p('prefix'), code=self._p('code'),
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Retirada', 'Retorno previsto', 'Total']
        rows = []
        for r in self._get_data():
            pickup_date = r.pickup.pickup_date.strftime('%d/%m/%Y') if hasattr(r, 'pickup') and r.pickup else '—'
            rows.append([
                f'#{r.number}', r.customer.name, pickup_date,
                r.return_date.strftime('%d/%m/%Y'), str(r.total_value),
            ])
        return self._csv_response(rows, headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'rentals': self._get_data(),
            'date_from': self._p('date_from'),
            'date_to': self._p('date_to'),
            'customer': self._p('customer'),
            'prefix': self._p('prefix'),
            'code': self._p('code'),
            'today': date_cls.today(),
        })
        return ctx


class DevolvidosReportView(_BaseReportView):
    """Devolvidos — por data efetiva de devolução (R11.04)."""
    template_name = 'reports/devolvidos.html'
    csv_filename = 'devolvidos.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self):
        return report_devolvidos(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), prefix=self._p('prefix'), code=self._p('code'),
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Retirada', 'Devolução efetiva', 'Total']
        rows = []
        for r in self._get_data():
            actual = r.return_record.return_date.strftime('%d/%m/%Y') if hasattr(r, 'return_record') and r.return_record else '—'
            pickup = r.pickup.pickup_date.strftime('%d/%m/%Y') if hasattr(r, 'pickup') and r.pickup else '—'
            rows.append([f'#{r.number}', r.customer.name, pickup, actual, str(r.total_value)])
        return self._csv_response(rows, headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'rentals': self._get_data(),
            'date_from': self._p('date_from'),
            'date_to': self._p('date_to'),
            'customer': self._p('customer'),
            'prefix': self._p('prefix'),
            'code': self._p('code'),
        })
        return ctx


class AtrasadosReportView(_BaseReportView):
    """Atrasados/não devolvidos — com dias de atraso (R11.05)."""
    template_name = 'reports/atrasados.html'
    csv_filename = 'atrasados.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self):
        return report_atrasados(
            customer=self._p('customer'), prefix=self._p('prefix'), code=self._p('code'),
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Retorno previsto', 'Dias de atraso', 'Total']
        rows = []
        for entry in self._get_data():
            r = entry['rental']
            rows.append([f'#{r.number}', r.customer.name,
                         r.return_date.strftime('%d/%m/%Y'), entry['days_late'], str(r.total_value)])
        return self._csv_response(rows, headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'overdue': self._get_data(),
            'customer': self._p('customer'),
            'prefix': self._p('prefix'),
            'code': self._p('code'),
            'today': date_cls.today(),
        })
        return ctx


class LocacoesReportView(_BaseReportView):
    """Locações realizadas — equiv. vendas.rpt (R11.06)."""
    template_name = 'reports/locacoes.html'
    csv_filename = 'locacoes.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self):
        return report_locacoes(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), status=self._p('status'),
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Uso/Evento', 'Retirada', 'Retorno previsto', 'Status', 'Total']
        rows = []
        for r in self._get_data():
            rows.append([
                f'#{r.number}', r.customer.name, r.use_for or '',
                r.pickup_date.strftime('%d/%m/%Y'), r.return_date.strftime('%d/%m/%Y'),
                r.get_status_display(), str(r.total_value),
            ])
        return self._csv_response(rows, headers)

    def get_context_data(self, **kwargs):
        from rentals.models import Rental as RentalModel
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'rentals': self._get_data(),
            'date_from': self._p('date_from'),
            'date_to': self._p('date_to'),
            'customer': self._p('customer'),
            'status': self._p('status'),
            'status_choices': RentalModel.Status.choices,
        })
        return ctx


class ContasVencimentoReportView(_BaseReportView):
    """Contas a receber por vencimento — equiv. receber.rpt (R11.07)."""
    template_name = 'reports/contas_vencimento.html'
    csv_filename = 'contas_a_receber.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self):
        overdue_only = self._p('overdue') == '1'
        return report_contas_vencimento(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), overdue_only=overdue_only,
        )

    def _export_csv(self):
        qs, _ = self._get_data()
        headers = ['Vencimento', 'Locação', 'Cliente', 'Valor', 'Pago', 'Saldo']
        rows = []
        for rec in qs:
            rows.append([
                rec.due_date.strftime('%d/%m/%Y') if rec.due_date else '—',
                f'#{rec.rental.number}', rec.rental.customer.name,
                str(rec.amount), str(rec.paid_amount), str(rec.balance),
            ])
        return self._csv_response(rows, headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs, totals = self._get_data()
        ctx.update({
            'receivables': qs,
            'totals': totals,
            'date_from': self._p('date_from'),
            'date_to': self._p('date_to'),
            'customer': self._p('customer'),
            'overdue': self._p('overdue'),
            'today': date_cls.today(),
        })
        return ctx


class ContasClienteReportView(_BaseReportView):
    """Contas a receber por cliente — equiv. receberc.rpt (R11.08)."""
    template_name = 'reports/contas_cliente.html'
    csv_filename = 'contas_por_cliente.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self):
        return report_contas_cliente(
            customer=self._p('customer'), status=self._p('status'),
        )

    def _export_csv(self):
        headers = ['Cliente', 'Locação', 'Vencimento', 'Valor', 'Pago', 'Saldo']
        rows = []
        for group in self._get_data():
            for rec in group['receivables']:
                rows.append([
                    group['customer'].name, f'#{rec.rental.number}',
                    rec.due_date.strftime('%d/%m/%Y') if rec.due_date else '—',
                    str(rec.amount), str(rec.paid_amount), str(rec.balance),
                ])
        return self._csv_response(rows, headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'groups': self._get_data(),
            'customer': self._p('customer'),
            'status': self._p('status'),
        })
        return ctx


# Keep old ReportView for backward-compat redirect
class ReportView(ReportsAccessMixin, TemplateView):
    """Legacy combined report view — kept for backward compatibility."""
    template_name = 'reports/report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['report_types'] = [
            ('a_retirar', 'A Retirar', 'reports:a_retirar'),
            ('retirados', 'Retirados', 'reports:retirados'),
            ('devolvidos', 'Devolvidos', 'reports:devolvidos'),
            ('atrasados', 'Atrasados', 'reports:atrasados'),
            ('locacoes', 'Locações Realizadas', 'reports:locacoes'),
            ('contas_vencimento', 'Contas a Receber por Vencimento', 'reports:contas_vencimento'),
            ('contas_cliente', 'Contas a Receber por Cliente', 'reports:contas_cliente'),
        ]
        return context
