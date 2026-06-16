from datetime import date as date_cls

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views import View
from django.views.generic import (
    CreateView, DeleteView, DetailView, FormView, ListView, TemplateView,
    UpdateView,
)

from billing.services import generate_for_rental, register_payment
from company.models import Company
from core.mixins import ModuleAccessMixin, ActionRequiredMixin

from .forms import RentalCancelForm, RentalForm, RentalItemFormSet
from .models import Rental, RentalItem


class RentalAccessMixin(ModuleAccessMixin):
    module_key = 'rentals'


# ── List ──────────────────────────────────────────────────────────────────────

class RentalListView(RentalAccessMixin, ListView):
    model = Rental
    template_name = 'rentals/rental_list.html'
    context_object_name = 'rentals'
    paginate_by = 20

    def get_queryset(self):
        qs = super().get_queryset().select_related('customer')
        q = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '')
        if q:
            qs = qs.filter(customer__name__icontains=q)
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        ctx['status_filter'] = self.request.GET.get('status', '')
        ctx['status_choices'] = Rental.Status.choices
        return ctx


# ── Detail ────────────────────────────────────────────────────────────────────

class RentalDetailView(RentalAccessMixin, DetailView):
    model = Rental
    template_name = 'rentals/rental_detail.html'
    context_object_name = 'rental'

    def get_queryset(self):
        items = RentalItem.objects.select_related('product').defer('proof_photo')
        return super().get_queryset().select_related('customer').prefetch_related(
            Prefetch('items', queryset=items)
        )


# ── Photo ─────────────────────────────────────────────────────────────────────

class RentalItemProofPhotoView(RentalAccessMixin, View):
    def get(self, request, *args, **kwargs):
        item = get_object_or_404(
            RentalItem.objects.only(
                'proof_photo',
                'proof_photo_content_type',
                'proof_photo_filename',
            ),
            pk=kwargs['pk'],
        )
        if not item.proof_photo:
            raise Http404('Foto não encontrada.')
        try:
            photo_file = item.proof_photo.open('rb')
        except (FileNotFoundError, OSError, ValueError):
            raise Http404('Foto não encontrada.')
        response = FileResponse(
            photo_file,
            content_type=item.proof_photo_content_type or 'image/jpeg',
        )
        response['Cache-Control'] = 'private, max-age=3600'
        disposition = content_disposition_header(
            as_attachment=False,
            filename=item.proof_photo_filename or 'foto-comprovacao.jpg',
        )
        if disposition:
            response['Content-Disposition'] = disposition
        return response


# ── Create ────────────────────────────────────────────────────────────────────

class RentalCreateView(RentalAccessMixin, CreateView):
    """Create rental + items + receivables + optional down payment in one transaction (R7.01/R7.05/R7.06)."""

    model = Rental
    form_class = RentalForm
    template_name = 'rentals/rental_form.html'
    success_url = reverse_lazy('rentals:list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'items' not in context:
            if self.request.POST:
                context['items'] = RentalItemFormSet(
                    self.request.POST,
                    self.request.FILES,
                )
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

            # R7.05 — generate installments if requested
            installment_count = form.cleaned_data.get('installment_count') or 0
            first_due_date = form.cleaned_data.get('first_due_date')
            receivables = []
            if installment_count and installment_count >= 1:
                receivables = generate_for_rental(
                    rental,
                    installments=installment_count,
                    first_due_date=first_due_date,
                )

            # R7.06 — register down payment against first receivable if provided
            dp_amount = form.cleaned_data.get('down_payment_amount')
            dp_method = form.cleaned_data.get('down_payment_method')
            dp_date = form.cleaned_data.get('down_payment_date')
            if dp_amount and dp_amount > 0 and receivables:
                register_payment(
                    receivables[0],
                    amount=dp_amount,
                    payment_date=dp_date or date_cls.today(),
                    method=dp_method or 'cash',
                    notes='Entrada na criação da locação',
                    user=self.request.user,
                )

        self.object = rental
        messages.success(self.request, f'Locação #{rental.number} criada com sucesso.')
        return HttpResponseRedirect(self.get_success_url())


# ── Update ────────────────────────────────────────────────────────────────────

class RentalUpdateView(RentalAccessMixin, UpdateView):
    """Edit a rental. Restricted when payments exist (R7.09)."""

    model = Rental
    form_class = RentalForm
    template_name = 'rentals/rental_form.html'

    def get_object(self, queryset=None):
        rental = super().get_object(queryset)
        if rental.status == Rental.Status.CANCELLED:
            messages.error(self.request, 'Não é possível editar uma locação cancelada.')
            raise Http404
        if rental.status == Rental.Status.RETURNED:
            messages.error(self.request, 'Não é possível editar uma locação devolvida.')
            raise Http404
        return rental

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        has_payments = self.object.receivables.filter(payments__isnull=False).exists()
        context['has_payments'] = has_payments
        if 'items' not in context:
            if self.request.POST:
                context['items'] = RentalItemFormSet(
                    self.request.POST,
                    self.request.FILES,
                    instance=self.object,
                )
            else:
                context['items'] = RentalItemFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        items = context['items']

        # Block item/date edits if payments exist and status != pending
        if context['has_payments'] and self.object.status != Rental.Status.PENDING:
            messages.error(
                self.request,
                'Existem pagamentos registrados. Somente observações podem ser editadas.',
            )
            return self.form_invalid(form)

        if not items.is_valid():
            return self.form_invalid(form)

        with transaction.atomic():
            rental = form.save(commit=False)
            rental.save()
            # Skip items update if has_payments (protect item history)
            if not context['has_payments']:
                items.instance = rental
                items.save()
                rental.recalculate_total()

        messages.success(self.request, f'Locação #{rental.number} atualizada.')
        return HttpResponseRedirect(rental.get_absolute_url())


# ── Cancel ────────────────────────────────────────────────────────────────────

class RentalCancelView(RentalAccessMixin, ActionRequiredMixin, FormView):
    """Cancel a rental with mandatory reason (R7.10)."""

    action_key = 'rentals.cancel'

    template_name = 'rentals/rental_cancel.html'
    form_class = RentalCancelForm

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['pk'])
        if self.rental.status in (Rental.Status.CANCELLED, Rental.Status.RETURNED):
            messages.error(request, 'Esta locação não pode ser cancelada.')
            return redirect(self.rental.get_absolute_url())
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['rental'] = self.rental
        return ctx

    def form_valid(self, form):
        with transaction.atomic():
            self.rental.status = Rental.Status.CANCELLED
            self.rental.cancelled_reason = form.cleaned_data['reason']
            self.rental.cancelled_at = timezone.now()
            self.rental.cancelled_by = self.request.user
            self.rental.save(update_fields=[
                'status', 'cancelled_reason', 'cancelled_at', 'cancelled_by', 'updated_at',
            ])
        from core.models import AuditLog
        AuditLog.objects.create(
            user=self.request.user,
            action='rental_cancel',
            model_name='Rental',
            object_id=str(self.rental.pk),
            object_repr=f'Locação #{self.rental.number}',
            reason=form.cleaned_data['reason'],
        )
        messages.success(self.request, f'Locação #{self.rental.number} cancelada.')
        return redirect(self.rental.get_absolute_url())


# ── Delete ────────────────────────────────────────────────────────────────────

class RentalDeleteView(RentalAccessMixin, ActionRequiredMixin, View):
    """Physically delete a rental only when no movement or payment exists (R7.11)."""

    action_key = 'rentals.delete'

    def get(self, request, *args, **kwargs):
        rental = get_object_or_404(Rental, pk=kwargs['pk'])
        return self._render_confirm(request, rental)

    def post(self, request, *args, **kwargs):
        rental = get_object_or_404(Rental, pk=kwargs['pk'])

        has_pickup = hasattr(rental, 'pickup') and rental.pickup is not None
        has_return = hasattr(rental, 'return_record') and rental.return_record is not None
        has_payments = rental.receivables.filter(payments__isnull=False).exists()

        if has_pickup or has_return or has_payments:
            messages.error(
                request,
                'Não é possível excluir esta locação pois já possui retirada, devolução ou '
                'pagamento registrados. Use o cancelamento.',
            )
            return redirect(rental.get_absolute_url())

        if rental.status == Rental.Status.CANCELLED:
            from core.models import AuditLog
            number = rental.number
            rental_pk = rental.pk
            AuditLog.objects.create(
                user=request.user,
                action='rental_delete',
                model_name='Rental',
                object_id=str(rental_pk),
                object_repr=f'Locação #{number}',
                reason='Exclusão física de locação cancelada.',
            )
            rental.delete()
            messages.success(request, f'Locação #{number} excluída.')
            return redirect('rentals:list')

        messages.error(
            request,
            'Apenas locações canceladas podem ser excluídas. Cancele primeiro.',
        )
        return redirect(rental.get_absolute_url())

    def _render_confirm(self, request, rental):
        from django.template.response import TemplateResponse
        return TemplateResponse(request, 'rentals/rental_delete_confirm.html', {'rental': rental})


# ── Contract ──────────────────────────────────────────────────────────────────

CONTRACT_VERSION = 'v1'


class RentalContractView(RentalAccessMixin, TemplateView):
    """Print-friendly rental contract (R7.07/R7.08)."""

    template_name = 'rentals/rental_contract.html'

    def get(self, request, *args, **kwargs):
        rental = get_object_or_404(
            Rental.objects.select_related('customer').prefetch_related(
                Prefetch(
                    'items',
                    queryset=RentalItem.objects.select_related('product').defer('proof_photo'),
                )
            ),
            pk=kwargs['pk'],
        )
        # R7.08 — stamp first print
        if not rental.contract_printed_at:
            Rental.objects.filter(pk=rental.pk).update(
                contract_version=CONTRACT_VERSION,
                contract_printed_at=timezone.now(),
            )
            rental.contract_version = CONTRACT_VERSION
            rental.contract_printed_at = timezone.now()

        company = Company.load()
        receivables = rental.receivables.order_by('due_date')
        return self.render_to_response(self.get_context_data(
            rental=rental,
            company=company,
            receivables=receivables,
            contract_version=CONTRACT_VERSION,
            copy_labels=['1ª via — Locatário', '2ª via — Empresa'],
        ))
