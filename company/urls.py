from django.urls import path

from .views import CompanySendWhatsAppReportNowView, CompanyUpdateView

app_name = 'company'

urlpatterns = [
    path('', CompanyUpdateView.as_view(), name='edit'),
    path('whatsapp-relatorio/reenviar/', CompanySendWhatsAppReportNowView.as_view(), name='resend_whatsapp_report'),
]
