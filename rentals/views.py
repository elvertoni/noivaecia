from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView

from company.models import Company
from core.mixins import ModuleAccessMixin

from .forms import RentalForm, RentalItemFormSet
from .models import Rental


class RentalAccessMixin(ModuleAccessMixin):
    module_key = 'rentals'


class RentalListView(RentalAccessMixin, ListView):
    model = Rental
    template_name = 'rentals/rental_list.html'
    context_object_name = 'rentals'
    paginate_by = 20

    def get_queryset(self):
        return super().get_queryset().select_related('customer')


class RentalDetailView(RentalAccessMixin, DetailView):
    model = Rental
    template_name = 'rentals/rental_detail.html'
    context_object_name = 'rental'

    def get_queryset(self):
        return super().get_queryset().select_related('customer')


class RentalCreateView(RentalAccessMixin, CreateView):
    """Create a rental with its items in one transaction (RF-15, RF-16)."""

    model = Rental
    form_class = RentalForm
    template_name = 'rentals/rental_form.html'
    success_url = reverse_lazy('rentals:list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'items' not in context:
            if self.request.POST:
                context['items'] = RentalItemFormSet(self.request.POST)
            else:
                context['items'] = RentalItemFormSet()
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        items = context['items']
        if not items.is_valid():
            return self.form_invalid(form)

        with transaction.atomic():
            rental = form.save(commit=False)
            rental.number = Company.next_rental_number()
            rental.save()
            items.instance = rental
            items.save()
            rental.recalculate_total()

        self.object = rental
        messages.success(self.request, f'Locação #{rental.number} criada com sucesso.')
        return HttpResponseRedirect(self.get_success_url())
