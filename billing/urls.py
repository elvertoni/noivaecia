from django.urls import path

from .views import (
    CashMovementListView,
    CashMovementReportView,
    CustomerReceivableView,
    FinancialDashboardView,
    GenerateReceivablesView,
    GlobalReceivableListView,
    ManualCashMovementView,
    MultiPayView,
    PaymentReportView,
    PaymentReversalView,
    PaymentView,
    ReceivableListView,
    ReceivablePayView,
    ReconciliationExportView,
    ReconciliationView,
)

app_name = 'billing'

urlpatterns = [
    # Global financial module (R5.01)
    path('', FinancialDashboardView.as_view(), name='dashboard'),
    path('titulos/', GlobalReceivableListView.as_view(), name='receivables'),
    path('cliente/', CustomerReceivableView.as_view(), name='customer_receivables_search'),
    path('cliente/<int:pk>/', CustomerReceivableView.as_view(), name='customer_receivables'),
    path('cliente/<int:pk>/baixar/', MultiPayView.as_view(), name='multi_pay'),
    path('titulo/<int:pk>/baixar/', ReceivablePayView.as_view(), name='pay_receivable'),
    path('pagamento/<int:pk>/estornar/', PaymentReversalView.as_view(), name='reverse_payment'),
    # R6 — cash, reports, reconciliation
    path('caixa/', CashMovementListView.as_view(), name='cash_movements'),
    path('caixa/novo/', ManualCashMovementView.as_view(), name='new_movement'),
    path('relatorio/recebimentos/', PaymentReportView.as_view(), name='payment_report'),
    path('relatorio/caixa/', CashMovementReportView.as_view(), name='cash_report'),
    path('reconciliacao/', ReconciliationView.as_view(), name='reconciliation'),
    path('reconciliacao/exportar/', ReconciliationExportView.as_view(), name='reconciliation_export'),
    # Rental-scoped (kept for rental detail integration)
    path('locacao/<int:rental_pk>/', ReceivableListView.as_view(), name='list'),
    path('locacao/<int:rental_pk>/gerar/', GenerateReceivablesView.as_view(), name='generate'),
    path('parcela/<int:pk>/pagar/', PaymentView.as_view(), name='pay'),
]
