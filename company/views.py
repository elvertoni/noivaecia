from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from core.mixins import ModuleAccessMixin

from .forms import CompanyForm
from .models import Company


class CompanyUpdateView(ModuleAccessMixin, SuccessMessageMixin, UpdateView):
    """Edit the singleton company configuration (RF-14)."""

    module_key = 'company'
    form_class = CompanyForm
    template_name = 'company/company_form.html'
    success_url = reverse_lazy('company:edit')
    success_message = 'Configuração da empresa atualizada com sucesso.'

    def get_object(self, queryset=None):
        return Company.load()
