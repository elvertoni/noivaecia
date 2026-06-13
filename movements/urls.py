from django.urls import path

from .views import (
    OverdueListView,
    PickupCreateView,
    PickupListView,
    ReturnCreateView,
    ReturnListView,
)

app_name = 'movements'

urlpatterns = [
    path('a-retirar/', PickupListView.as_view(), name='pickup_list'),
    path('retirados/', ReturnListView.as_view(), name='return_list'),
    path('atrasados/', OverdueListView.as_view(), name='overdue_list'),
    path('locacao/<int:rental_pk>/retirada/', PickupCreateView.as_view(), name='pickup'),
    path('locacao/<int:rental_pk>/devolucao/', ReturnCreateView.as_view(), name='return'),
]
