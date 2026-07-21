from django.urls import path

from .views import WhatsAppConnectionView, WhatsAppDispatchView, WhatsAppPanelView

app_name = 'notifications'

urlpatterns = [
    path('', WhatsAppPanelView.as_view(), name='whatsapp_panel'),
    path('conexao/', WhatsAppConnectionView.as_view(), name='connection'),
    path('disparar/', WhatsAppDispatchView.as_view(), name='dispatch'),
]
