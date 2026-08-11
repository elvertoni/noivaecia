import logging

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.management import call_command
from django.core.management.base import CommandError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import UpdateView

from core.mixins import ActionRequiredMixin, ModuleAccessMixin

from .forms import CompanyForm
from .models import Company

logger = logging.getLogger(__name__)


class CompanyUpdateView(ModuleAccessMixin, ActionRequiredMixin, SuccessMessageMixin, UpdateView):
    """Edit the singleton company configuration (RF-14)."""

    module_key = 'company'
    action_key = 'company.manage'
    form_class = CompanyForm
    template_name = 'company/company_form.html'
    success_url = reverse_lazy('company:edit')
    success_message = 'Configuração da empresa atualizada com sucesso.'

    def get_object(self, queryset=None):
        return Company.load()


class CompanySendWhatsAppReportNowView(ModuleAccessMixin, ActionRequiredMixin, View):
    """Trigger an immediate WhatsApp daily report send, bypassing the
    once-a-day-per-recipient lock that protects the background scheduler
    from resending every 30s. An explicit click here is a deliberate
    request, so it forces a resend to every configured recipient."""

    module_key = 'company'
    action_key = 'notifications.send'

    def post(self, request, *args, **kwargs):
        company = Company.load()
        if not company.whatsapp_reports_enabled or not company.whatsapp_report_number.strip():
            messages.error(
                request,
                'Ative o relatório diário e informe ao menos um número antes de reenviar.',
            )
            return redirect('company:edit')

        try:
            call_command('send_daily_whatsapp_report', force=True)
        except CommandError:
            logger.exception('Manual WhatsApp report dispatch failed')
            messages.error(
                request,
                'Não foi possível reenviar o relatório agora. Tente novamente; '
                'se o problema continuar, consulte os registros do servidor.',
            )
        else:
            messages.success(request, 'Relatório reenviado para os destinatários configurados.')
        return redirect('company:edit')
