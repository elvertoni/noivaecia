from django.urls import path

from .views import DashboardView, healthz

urlpatterns = [
    path('healthz/', healthz, name='healthz'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
]
