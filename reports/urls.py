from django.urls import path

from .views import ReportView

app_name = 'reports'

urlpatterns = [
    path('', ReportView.as_view(), name='report'),
]
