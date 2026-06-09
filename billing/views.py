from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import FormView, ListView

from core.mixins import ModuleAccessMixin
from rentals.models import Rental

from .forms import GenerateReceivablesForm, PaymentForm
from .models import Receivable
from .services import generate_for_rental, total_with_interest


class BillingAccessMixin(ModuleAccessMixin):
    module_key = 'billing'


class ReceivableListView(BillingAccessMixin, ListView):
    """List receivables of a rental with total including interest (RF-19, RF-20)."""

    template_name = 'billing/receivable_list.html'
    context_object_name = 'receivables'

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Receivable.objects.filter(rental=self.rental)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = []
        for receivable in context['receivables']:
            rows.append({
                'obj': receivable,
                'total_with_interest': total_with_interest(receivable),
            })
        context['rows'] = rows
        context['rental'] = self.rental
        context['generate_form'] = GenerateReceivablesForm()
        return context


class GenerateReceivablesView(BillingAccessMixin, FormView):
    """Generate installments for a rental (RF-19 / 8.1.3)."""

    form_class = GenerateReceivablesForm

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        generate_for_rental(
            self.rental,
            installments=form.cleaned_data['installments'],
            first_due_date=form.cleaned_data.get('first_due_date'),
        )
        messages.success(self.request, 'Parcelas geradas com sucesso.')
        return redirect('billing:list', rental_pk=self.rental.pk)

    def form_invalid(self, form):
        messages.error(self.request, 'Não foi possível gerar as parcelas.')
        return redirect('billing:list', rental_pk=self.rental.pk)


class PaymentView(BillingAccessMixin, FormView):
    """Register a payment against a receivable (RF-21)."""

    form_class = PaymentForm
    template_name = 'billing/payment_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.receivable = get_object_or_404(Receivable, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['receivable'] = self.receivable
        context['total_with_interest'] = total_with_interest(self.receivable)
        return context

    def form_valid(self, form):
        self.receivable.register_payment(
            form.cleaned_data['value'], form.cleaned_data['payment_date']
        )
        messages.success(self.request, 'Pagamento registrado com sucesso.')
        return redirect('billing:list', rental_pk=self.receivable.rental_id)
