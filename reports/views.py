"""Reports module - isolated per-type services (R11.01)."""
import csv
from datetime import date as date_cls

from django.contrib import messages
from django.http import StreamingHttpResponse
from django.views.generic import TemplateView

from core.mixins import ModuleAccessMixin
from core.ui import parse_br_date

from .services import (
    DEFAULT_REPORT_LIMIT,
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


class _CSVBuffer:
    def write(self, value):
        return value


def _fmt_date(value):
    return value.strftime('%d/%m/%Y') if value else '—'


def _fmt_use_for(value):
    parsed = parse_br_date(value)
    return parsed.strftime('%d/%m/%Y') if parsed else (value or '')


class _BaseReportView(ReportsAccessMixin, TemplateView):
    """Base with common filter param helpers and CSV export (R11.09/R11.10)."""
    csv_filename = 'relatorio.csv'
    report_limit = DEFAULT_REPORT_LIMIT

    def _p(self, key, default=''):
        return self.request.GET.get(key, default).strip()

    def _date_input(self, key):
        value = parse_br_date(self._p(key))
        return value.isoformat() if value else ''

    def get(self, request, *args, **kwargs):
        if any(self._p(key) and not parse_br_date(self._p(key)) for key in ('date_from', 'date_to')):
            messages.error(request, 'Informe as datas no formato dd/mm/aaaa.')
        return super().get(request, *args, **kwargs)

    def _csv_response(self, rows, headers):
        """Stream CSV rows with UTF-8 BOM for Excel (R11.10)."""
        from django.core.exceptions import PermissionDenied
        if not self.request.user.has_action('reports.export'):
            raise PermissionDenied

        writer = csv.writer(_CSVBuffer(), delimiter=';')

        def stream():
            yield '\ufeff'
            yield writer.writerow(headers)
            for row in rows:
                yield writer.writerow(row)

        response = StreamingHttpResponse(stream(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{self.csv_filename}"'
        return response

    def _limit_for_export(self):
        return self.report_limit


class ARetirarReportView(_BaseReportView):
    """Produtos a retirar - equiv. locados.rpt não retirados (R11.02)."""
    template_name = 'reports/a_retirar.html'
    csv_filename = 'a_retirar.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self, max_results=DEFAULT_REPORT_LIMIT):
        return report_a_retirar(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), prefix=self._p('prefix'), code=self._p('code'),
            max_results=max_results,
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Usar em', 'Retirada', 'Retorno previsto', 'Total']

        def rows():
            for rental in self._get_data(max_results=self._limit_for_export()):
                yield [
                    f'#{rental.number}',
                    rental.customer.name,
                    _fmt_use_for(rental.use_for),
                    _fmt_date(rental.pickup_date),
                    _fmt_date(rental.return_date),
                    str(rental.total_value),
                ]

        return self._csv_response(rows(), headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'rentals': self._get_data(),
            'date_from': self._date_input('date_from'),
            'date_to': self._date_input('date_to'),
            'customer': self._p('customer'),
            'prefix': self._p('prefix'),
            'code': self._p('code'),
            'today': date_cls.today(),
            'report_limit': self.report_limit,
        })
        return ctx


class RetiradosReportView(_BaseReportView):
    """Produtos retirados - equiv. locados12.rpt (R11.03)."""
    template_name = 'reports/retirados.html'
    csv_filename = 'retirados.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self, max_results=DEFAULT_REPORT_LIMIT):
        return report_retirados(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), prefix=self._p('prefix'), code=self._p('code'),
            max_results=max_results,
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Retirada', 'Retorno previsto', 'Total']

        def rows():
            for rental in self._get_data(max_results=self._limit_for_export()):
                pickup = rental.pickup.pickup_date if hasattr(rental, 'pickup') and rental.pickup else None
                yield [
                    f'#{rental.number}',
                    rental.customer.name,
                    _fmt_date(pickup),
                    _fmt_date(rental.return_date),
                    str(rental.total_value),
                ]

        return self._csv_response(rows(), headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'rentals': self._get_data(),
            'date_from': self._date_input('date_from'),
            'date_to': self._date_input('date_to'),
            'customer': self._p('customer'),
            'prefix': self._p('prefix'),
            'code': self._p('code'),
            'today': date_cls.today(),
            'report_limit': self.report_limit,
        })
        return ctx


class DevolvidosReportView(_BaseReportView):
    """Devolvidos - por data efetiva de devolução (R11.04)."""
    template_name = 'reports/devolvidos.html'
    csv_filename = 'devolvidos.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self, max_results=DEFAULT_REPORT_LIMIT):
        return report_devolvidos(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), prefix=self._p('prefix'), code=self._p('code'),
            max_results=max_results,
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Retirada', 'Devolução efetiva', 'Total']

        def rows():
            for rental in self._get_data(max_results=self._limit_for_export()):
                pickup = rental.pickup.pickup_date if hasattr(rental, 'pickup') and rental.pickup else None
                returned = (
                    rental.return_record.return_date
                    if hasattr(rental, 'return_record') and rental.return_record
                    else None
                )
                yield [
                    f'#{rental.number}',
                    rental.customer.name,
                    _fmt_date(pickup),
                    _fmt_date(returned),
                    str(rental.total_value),
                ]

        return self._csv_response(rows(), headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'rentals': self._get_data(),
            'date_from': self._date_input('date_from'),
            'date_to': self._date_input('date_to'),
            'customer': self._p('customer'),
            'prefix': self._p('prefix'),
            'code': self._p('code'),
            'report_limit': self.report_limit,
        })
        return ctx


class AtrasadosReportView(_BaseReportView):
    """Atrasados/não devolvidos - com dias de atraso (R11.05)."""
    template_name = 'reports/atrasados.html'
    csv_filename = 'atrasados.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self, max_results=DEFAULT_REPORT_LIMIT):
        return report_atrasados(
            customer=self._p('customer'), prefix=self._p('prefix'), code=self._p('code'),
            max_results=max_results,
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Retorno previsto', 'Dias de atraso', 'Total']

        def rows():
            for entry in self._get_data(max_results=self._limit_for_export()):
                rental = entry['rental']
                yield [
                    f'#{rental.number}',
                    rental.customer.name,
                    _fmt_date(rental.return_date),
                    entry['days_late'],
                    str(rental.total_value),
                ]

        return self._csv_response(rows(), headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'overdue': self._get_data(),
            'customer': self._p('customer'),
            'prefix': self._p('prefix'),
            'code': self._p('code'),
            'today': date_cls.today(),
            'report_limit': self.report_limit,
        })
        return ctx


class LocacoesReportView(_BaseReportView):
    """Locações realizadas - equiv. vendas.rpt (R11.06)."""
    template_name = 'reports/locacoes.html'
    csv_filename = 'locacoes.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self, max_results=DEFAULT_REPORT_LIMIT):
        return report_locacoes(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), status=self._p('status'),
            max_results=max_results,
        )

    def _export_csv(self):
        headers = ['Locação', 'Cliente', 'Usar em', 'Retirada', 'Retorno previsto', 'Status', 'Total']

        def rows():
            for rental in self._get_data(max_results=self._limit_for_export()):
                yield [
                    f'#{rental.number}',
                    rental.customer.name,
                    _fmt_use_for(rental.use_for),
                    _fmt_date(rental.pickup_date),
                    _fmt_date(rental.return_date),
                    rental.get_status_display(),
                    str(rental.total_value),
                ]

        return self._csv_response(rows(), headers)

    def get_context_data(self, **kwargs):
        from rentals.models import Rental as RentalModel
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'rentals': self._get_data(),
            'date_from': self._date_input('date_from'),
            'date_to': self._date_input('date_to'),
            'customer': self._p('customer'),
            'status': self._p('status'),
            'status_choices': RentalModel.Status.choices,
            'report_limit': self.report_limit,
        })
        return ctx


class ContasVencimentoReportView(_BaseReportView):
    """Contas a receber por vencimento - equiv. receber.rpt (R11.07)."""
    template_name = 'reports/contas_vencimento.html'
    csv_filename = 'contas_a_receber.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self, max_results=DEFAULT_REPORT_LIMIT):
        overdue_only = self._p('overdue') == '1'
        return report_contas_vencimento(
            date_from=self._p('date_from'), date_to=self._p('date_to'),
            customer=self._p('customer'), overdue_only=overdue_only,
            max_results=max_results,
        )

    def _export_csv(self):
        receivables, _ = self._get_data(max_results=self._limit_for_export())
        headers = ['Vencimento', 'Locação', 'Cliente', 'Valor', 'Pago', 'Saldo']

        def rows():
            for receivable in receivables:
                yield [
                    _fmt_date(receivable.due_date),
                    f'#{receivable.rental.number}',
                    receivable.rental.customer.name,
                    str(receivable.amount),
                    str(receivable.paid_amount),
                    str(receivable.balance),
                ]

        return self._csv_response(rows(), headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        receivables, totals = self._get_data()
        ctx.update({
            'receivables': receivables,
            'totals': totals,
            'date_from': self._date_input('date_from'),
            'date_to': self._date_input('date_to'),
            'customer': self._p('customer'),
            'overdue': self._p('overdue'),
            'today': date_cls.today(),
            'report_limit': self.report_limit,
        })
        return ctx


class ContasClienteReportView(_BaseReportView):
    """Contas a receber por cliente - equiv. receberc.rpt (R11.08)."""
    template_name = 'reports/contas_cliente.html'
    csv_filename = 'contas_por_cliente.csv'

    def get(self, request, *args, **kwargs):
        if self._p('format') == 'csv':
            return self._export_csv()
        return super().get(request, *args, **kwargs)

    def _get_data(self, max_results=DEFAULT_REPORT_LIMIT):
        return report_contas_cliente(
            customer=self._p('customer'), status=self._p('status'),
            max_results=max_results,
        )

    def _export_csv(self):
        headers = ['Cliente', 'Locação', 'Vencimento', 'Valor', 'Pago', 'Saldo']

        def rows():
            for group in self._get_data(max_results=self._limit_for_export()):
                for receivable in group['receivables']:
                    yield [
                        group['customer'].name,
                        f'#{receivable.rental.number}',
                        _fmt_date(receivable.due_date),
                        str(receivable.amount),
                        str(receivable.paid_amount),
                        str(receivable.balance),
                    ]

        return self._csv_response(rows(), headers)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'groups': self._get_data(),
            'customer': self._p('customer'),
            'status': self._p('status'),
            'report_limit': self.report_limit,
        })
        return ctx


class ReportView(ReportsAccessMixin, TemplateView):
    """Legacy combined report view - kept for backward compatibility."""
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
