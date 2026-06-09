from django.urls import path

from .views import RentalCreateView, RentalDetailView, RentalListView

app_name = 'rentals'

urlpatterns = [
    path('', RentalListView.as_view(), name='list'),
    path('nova/', RentalCreateView.as_view(), name='create'),
    path('<int:pk>/', RentalDetailView.as_view(), name='detail'),
]
