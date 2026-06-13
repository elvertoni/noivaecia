from django.urls import path

from .views import (
    RentalCancelView,
    RentalContractView,
    RentalCreateView,
    RentalDeleteView,
    RentalDetailView,
    RentalItemProofPhotoView,
    RentalListView,
    RentalUpdateView,
)

app_name = 'rentals'

urlpatterns = [
    path('', RentalListView.as_view(), name='list'),
    path('nova/', RentalCreateView.as_view(), name='create'),
    path('itens/<int:pk>/foto/', RentalItemProofPhotoView.as_view(), name='item_photo'),
    path('<int:pk>/', RentalDetailView.as_view(), name='detail'),
    path('<int:pk>/editar/', RentalUpdateView.as_view(), name='update'),
    path('<int:pk>/cancelar/', RentalCancelView.as_view(), name='cancel'),
    path('<int:pk>/excluir/', RentalDeleteView.as_view(), name='delete'),
    path('<int:pk>/contrato/', RentalContractView.as_view(), name='contract'),
]
