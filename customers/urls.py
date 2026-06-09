from django.urls import path

from .views import (
    CustomerCreateView,
    CustomerDeleteView,
    CustomerListView,
    CustomerUpdateView,
)

app_name = 'customers'

urlpatterns = [
    path('', CustomerListView.as_view(), name='list'),
    path('novo/', CustomerCreateView.as_view(), name='create'),
    path('<int:pk>/editar/', CustomerUpdateView.as_view(), name='update'),
    path('<int:pk>/excluir/', CustomerDeleteView.as_view(), name='delete'),
]
