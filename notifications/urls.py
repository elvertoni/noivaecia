from django.urls import path

from .views import WhatsAppDispatchView, WhatsAppPanelView

app_name = 'notifications'

urlpatterns = [
    path('', WhatsAppPanelView.as_view(), name='whatsapp_panel'),
    path('disparar/', WhatsAppDispatchView.as_view(), name='dispatch'),
]
