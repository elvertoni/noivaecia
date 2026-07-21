"""Review-and-dispatch panel for customer WhatsApp reminders.

Ana opens this screen, checks who is queued to receive a pickup/return
reminder, and triggers the actual send. All business logic (queue building,
message rendering, idempotent dispatch) lives in ``notifications.services`` —
this module only wires it to a view and a template.
"""
import time
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ModuleAccessMixin
from rentals.models import Rental

from .models import CustomerMessage
from . import evolution
from .services import (
    MessageTemplateError,
    dispatch_customer_message,
    get_default_message_template,
    pickup_reminder_queue,
    return_reminder_queue,
    validate_message_template,
)

# Anti-ban throttle between real sends. A module-level constant so tests can
# override it (or ``time.sleep`` itself) to avoid slowing the suite down.
SEND_SPACING_SECONDS = 0.3

RECENT_MESSAGES_LIMIT = 20
_TEMPLATE_DRAFT_SESSION_KEY = 'notifications_message_template_drafts'

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
        template_drafts = self.request.session.pop(_TEMPLATE_DRAFT_SESSION_KEY, {})
        evolution_state = ''
        evolution_qrcode = None
        evolution_error = ''
        evolution_configured = all(
            getattr(settings, name, '')
            for name in ('EVOLUTION_API_URL', 'EVOLUTION_API_KEY', 'EVOLUTION_INSTANCE')
        )
        if evolution_configured:
            try:
                evolution_state = evolution.get_connection_state()
                if self.request.GET.get('connect') == '1' and evolution_state != 'open':
                    evolution_qrcode = evolution.connect_instance_qrcode()
            except evolution.EvolutionError as exc:
                evolution_error = str(exc)
        ctx.update({
            'pickup_items': pickup_reminder_queue(today=today),
            'return_items': return_reminder_queue(today=today),
            'tomorrow': today + timedelta(days=1),
            'today': today,
            'evolution_configured': evolution_configured,
            'evolution_connection_state': evolution_state,
            'evolution_qrcode': evolution_qrcode,
            'evolution_error': evolution_error,
            'pickup_message_template': template_drafts.get(
                CustomerMessage.Kind.PICKUP_REMINDER,
                get_default_message_template(CustomerMessage.Kind.PICKUP_REMINDER),
            ),
            'return_message_template': template_drafts.get(
                CustomerMessage.Kind.RETURN_REMINDER,
                get_default_message_template(CustomerMessage.Kind.RETURN_REMINDER),
            ),
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

        message_template = request.POST.get('message_template')
        if message_template is None:
            message_template = get_default_message_template(kind)
        try:
            message_template = validate_message_template(message_template)
        except MessageTemplateError as exc:
            request.session[_TEMPLATE_DRAFT_SESSION_KEY] = {kind: message_template}
            messages.error(request, str(exc))
            return redirect('notifications:whatsapp_panel')

        queue = _QUEUE_BUILDERS[kind]()
        eligible_rentals = {
            str(item['rental'].pk): item['rental']
            for item in queue
        }
        if request.POST.get('send_all'):
            rentals = list(eligible_rentals.values())
        else:
            rental_ids = request.POST.getlist('rental_ids')
            requested_ids = list(dict.fromkeys(rental_ids))
            rentals = [
                eligible_rentals[rental_id]
                for rental_id in requested_ids
                if rental_id in eligible_rentals
            ]
            if len(rentals) != len(requested_ids):
                messages.warning(
                    request,
                    'Uma ou mais locações não estão disponíveis para este aviso.',
                )

        sent_count = 0
        failed_count = 0
        for index, rental in enumerate(rentals):
            if index:
                time.sleep(SEND_SPACING_SECONDS)
            record = dispatch_customer_message(
                rental,
                kind,
                user=request.user,
                message_template=message_template,
            )
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
