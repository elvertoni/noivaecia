from django.urls import path

from .views import (
    RentalCreateView,
    RentalDetailView,
    RentalItemProofPhotoView,
    RentalListView,
)

app_name = 'rentals'

urlpatterns = [
    path('', RentalListView.as_view(), name='list'),
    path('nova/', RentalCreateView.as_view(), name='create'),
    path('itens/<int:pk>/foto/', RentalItemProofPhotoView.as_view(), name='item_photo'),
    path('<int:pk>/', RentalDetailView.as_view(), name='detail'),
]
