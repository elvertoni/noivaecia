from django.urls import path

from .views import (
    ARetirarReportView,
    AtrasadosReportView,
    ContasClienteReportView,
    ContasVencimentoReportView,
    DevolvidosReportView,
    LocacoesReportView,
    ReportView,
    ReportsIndexView,
    RetiradosReportView,
)

app_name = 'reports'

urlpatterns = [
    path('', ReportsIndexView.as_view(), name='index'),
    path('a-retirar/', ARetirarReportView.as_view(), name='a_retirar'),
    path('retirados/', RetiradosReportView.as_view(), name='retirados'),
    path('devolvidos/', DevolvidosReportView.as_view(), name='devolvidos'),
    path('atrasados/', AtrasadosReportView.as_view(), name='atrasados'),
    path('locacoes/', LocacoesReportView.as_view(), name='locacoes'),
    path('contas-vencimento/', ContasVencimentoReportView.as_view(), name='contas_vencimento'),
    path('contas-cliente/', ContasClienteReportView.as_view(), name='contas_cliente'),
    # kept for backward-compat
    path('legacy/', ReportView.as_view(), name='report'),
]
