"""Review-and-dispatch panel for customer WhatsApp reminders.

Ana opens this screen, checks who is queued to receive a pickup/return
reminder, and triggers the actual send. All business logic (queue building,
message rendering, idempotent dispatch) lives in ``notifications.services`` —
this module only wires it to a view and a template.
"""
import time
from datetime import timedelta

from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ModuleAccessMixin
from rentals.models import Rental

from .models import CustomerMessage
from .services import dispatch_customer_message, pickup_reminder_queue, return_reminder_queue

# Anti-ban throttle between real sends. A module-level constant so tests can
# override it (or ``time.sleep`` itself) to avoid slowing the suite down.
SEND_SPACING_SECONDS = 0.3

RECENT_MESSAGES_LIMIT = 20

_QUEUE_BUILDERS = {
    CustomerMessage.Kind.PICKUP_REMINDER: pickup_reminder_queue,
    CustomerMessage.Kind.RETURN_REMINDER: return_reminder_queue,
}


class NotificationsAccessMixin(ModuleAccessMixin):
    module_key = 'movements'


class WhatsAppPanelView(NotificationsAccessMixin, TemplateView):
    """Show today's pickup/return reminder queues for review before sending."""

    template_name = 'notifications/whatsapp_panel.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        ctx.update({
            'pickup_items': pickup_reminder_queue(today=today),
            'return_items': return_reminder_queue(today=today),
            'tomorrow': today + timedelta(days=1),
            'today': today,
            'recent_messages': (
                CustomerMessage.objects.select_related('rental', 'customer')
                .order_by('-created_at')[:RECENT_MESSAGES_LIMIT]
            ),
        })
        return ctx


class WhatsAppDispatchView(NotificationsAccessMixin, View):
    """Send the selected (or entire) reminder queue for one ``kind``."""

    def post(self, request, *args, **kwargs):
        kind = request.POST.get('kind')
        if kind not in _QUEUE_BUILDERS:
            messages.error(request, 'Tipo de aviso inválido.')
            return redirect('notifications:whatsapp_panel')

        if request.POST.get('send_all'):
            queue = _QUEUE_BUILDERS[kind]()
            rentals = [item['rental'] for item in queue]
        else:
            rental_ids = request.POST.getlist('rental_ids')
            rentals = list(Rental.objects.filter(pk__in=rental_ids))

        sent_count = 0
        failed_count = 0
        for index, rental in enumerate(rentals):
            if index:
                time.sleep(SEND_SPACING_SECONDS)
            record = dispatch_customer_message(rental, kind, user=request.user)
            if record.status == CustomerMessage.Status.SENT:
                sent_count += 1
            else:
                failed_count += 1

        if not rentals:
            messages.info(request, 'Nenhum destinatário selecionado.')
        else:
            messages.success(
                request,
                f'{sent_count} aviso(s) enviado(s), {failed_count} falha(s).',
            )
        return redirect('notifications:whatsapp_panel')
