from django.urls import path

from .views import (
    ImportQualityView,
    LegacyAuditView,
    MaintenanceView,
    RecalculateBalancesView,
    RecalculateRentalTotalsView,
    ReconcileFinancialView,
    SettleReturnsView,
)

app_name = 'maintenance'

urlpatterns = [
    path('', MaintenanceView.as_view(), name='index'),
    path('recalcular-totais/', RecalculateRentalTotalsView.as_view(), name='recalc_totals'),
    path('recalcular-saldos/', RecalculateBalancesView.as_view(), name='recalc_balances'),
    path('acerto-devolucoes/', SettleReturnsView.as_view(), name='settle_returns'),
    path('qualidade-importacao/', ImportQualityView.as_view(), name='import_quality'),
    path('auditoria-legado/', LegacyAuditView.as_view(), name='legacy_audit'),
    path('reconciliar/', ReconcileFinancialView.as_view(), name='reconcile'),
]
