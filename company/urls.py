from django.urls import path

from .views import CompanyUpdateView

app_name = 'company'

urlpatterns = [
    path('', CompanyUpdateView.as_view(), name='edit'),
]
