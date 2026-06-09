from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView

from core.mixins import ModuleAccessMixin
from rentals.models import Rental

from .forms import PickupForm, ReturnForm
from .services import compute_days_late, compute_penalty


class MovementsAccessMixin(ModuleAccessMixin):
    module_key = 'movements'


class PickupCreateView(MovementsAccessMixin, CreateView):
    """Register the pickup of a rental's items (RF-17)."""

    form_class = PickupForm
    template_name = 'movements/pickup_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        if hasattr(self.rental, 'pickup'):
            messages.info(request, 'Esta locação já teve a retirada registrada.')
            return redirect('rentals:detail', pk=self.rental.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['rental'] = self.rental
        return context

    def form_valid(self, form):
        form.instance.rental = self.rental
        self.object = form.save()
        messages.success(self.request, 'Retirada registrada com sucesso.')
        return redirect('rentals:detail', pk=self.rental.pk)


class ReturnCreateView(MovementsAccessMixin, CreateView):
    """Register the return of a rental, computing late days and penalty (RF-18)."""

    form_class = ReturnForm
    template_name = 'movements/return_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        if hasattr(self.rental, 'return_record'):
            messages.info(request, 'Esta locação já teve a devolução registrada.')
            return redirect('rentals:detail', pk=self.rental.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['rental'] = self.rental
        return context

    def form_valid(self, form):
        return_obj = form.save(commit=False)
        return_obj.rental = self.rental
        days_late = compute_days_late(self.rental.return_date, return_obj.return_date)
        return_obj.days_late = days_late
        return_obj.penalty_applied = compute_penalty(self.rental, days_late)
        return_obj.save()
        self.object = return_obj
        messages.success(
            self.request,
            f'Devolução registrada. Dias de atraso: {days_late}; '
            f'multa: R$ {return_obj.penalty_applied}.',
        )
        return redirect('rentals:detail', pk=self.rental.pk)
