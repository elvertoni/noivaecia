from django.urls import path

from .views import (
    MaintenanceView,
    RecalculateBalancesView,
    RecalculateRentalTotalsView,
)

app_name = 'maintenance'

urlpatterns = [
    path('', MaintenanceView.as_view(), name='index'),
    path('recalcular-totais/', RecalculateRentalTotalsView.as_view(), name='recalc_totals'),
    path('recalcular-saldos/', RecalculateBalancesView.as_view(), name='recalc_balances'),
]
