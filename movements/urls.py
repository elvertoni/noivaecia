from django.urls import path

from .views import PickupCreateView, ReturnCreateView

app_name = 'movements'

urlpatterns = [
    path('locacao/<int:rental_pk>/retirada/', PickupCreateView.as_view(), name='pickup'),
    path('locacao/<int:rental_pk>/devolucao/', ReturnCreateView.as_view(), name='return'),
]
