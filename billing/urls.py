from django.urls import path

from .views import GenerateReceivablesView, PaymentView, ReceivableListView

app_name = 'billing'

urlpatterns = [
    path('locacao/<int:rental_pk>/', ReceivableListView.as_view(), name='list'),
    path('locacao/<int:rental_pk>/gerar/', GenerateReceivablesView.as_view(), name='generate'),
    path('parcela/<int:pk>/pagar/', PaymentView.as_view(), name='pay'),
]
