from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.mixins import ModuleAccessMixin

from .forms import CustomerForm
from .models import Customer


class CustomerListView(ModuleAccessMixin, ListView):
    """Paginated, searchable customer listing (RF-11)."""

    module_key = 'customers'
    model = Customer
    template_name = 'customers/customer_list.html'
    context_object_name = 'customers'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('q', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(cpf__icontains=search)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('q', '')
        return context


class CustomerCreateView(ModuleAccessMixin, SuccessMessageMixin, CreateView):
    module_key = 'customers'
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'
    success_url = reverse_lazy('customers:list')
    success_message = 'Cliente cadastrado com sucesso.'


class CustomerUpdateView(ModuleAccessMixin, SuccessMessageMixin, UpdateView):
    module_key = 'customers'
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'
    success_url = reverse_lazy('customers:list')
    success_message = 'Cliente atualizado com sucesso.'


class CustomerDeleteView(ModuleAccessMixin, DeleteView):
    module_key = 'customers'
    model = Customer
    template_name = 'customers/customer_confirm_delete.html'
    success_url = reverse_lazy('customers:list')

    def form_valid(self, form):
        messages.success(self.request, 'Cliente excluído com sucesso.')
        return super().form_valid(form)
