from django.urls import path

from .views import (
    CustomerCreateView,
    CustomerDeactivateView,
    CustomerDeleteView,
    CustomerDetailView,
    CustomerListView,
    CustomerSearchView,
    CustomerUpdateView,
)

app_name = 'customers'

urlpatterns = [
    path('', CustomerListView.as_view(), name='list'),
    path('novo/', CustomerCreateView.as_view(), name='create'),
    path('<int:pk>/', CustomerDetailView.as_view(), name='detail'),
    path('<int:pk>/inativar/', CustomerDeactivateView.as_view(), name='deactivate'),
    path('<int:pk>/editar/', CustomerUpdateView.as_view(), name='update'),
    path('<int:pk>/excluir/', CustomerDeleteView.as_view(), name='delete'),
    path('busca/', CustomerSearchView.as_view(), name='search'),
]
