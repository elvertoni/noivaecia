from decimal import Decimal

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

from billing.services import (
    PaymentPlanError,
    apply_cancellation_penalty,
    compute_cancellation_penalty,
    create_rental_payment_plan,
)
from catalog.availability import find_overlapping_rentals
from catalog.models import Product
from company.models import Company
from core.mixins import ModuleAccessMixin, ActionRequiredMixin
from core.models import AuditLog
from customers.models import _normalize_name

from .forms import RentalCancelForm, RentalForm, RentalItemFormSet, RentalItemEditFormSet
from .models import Rental, RentalItem


def check_item_availability(items, pickup_date, return_date, exclude_rental_id=None):
    """Return PT-BR conflict messages for items double-booked in the window (R7.04).

    Skips deleted/empty rows. A product already held by another active rental
    whose dates overlap [pickup_date, return_date] is flagged so the rental can
    never be saved over a live booking.
    """
    if not (pickup_date and return_date):
        return []
    products = {}
    for item_form in items.forms:
        if not getattr(item_form, 'cleaned_data', None):
            continue
        if item_form.cleaned_data.get('DELETE'):
            continue
        product = item_form.cleaned_data.get('product')
        if not product or product.pk in products:
            continue
        products[product.pk] = product

    overlaps = find_overlapping_rentals(
        products,
        pickup_date,
        return_date,
        exclude_rental_id=exclude_rental_id,
    )
    conflicts = []
    for product_id, product in products.items():
        clash = overlaps.get(product_id)
        if clash:
            conflicts.append(
                f'A peça {product.category.prefix}{product.code} '
                f'({product.description or "sem descrição"}) já está alocada na '
                f'locação #{clash.number} de {clash.customer.name} '
                f'({clash.pickup_date:%d/%m/%Y} a {clash.return_date:%d/%m/%Y}).'
            )
    return conflicts


def lock_item_products(items):
    """Serialize normal create/edit flows that reserve the same physical pieces."""
    product_ids = {
        item_form.cleaned_data['product'].pk
        for item_form in items.forms
        if getattr(item_form, 'cleaned_data', None)
        and not item_form.cleaned_data.get('DELETE')
        and item_form.cleaned_data.get('product')
    }
    if product_ids:
        list(
            Product.objects.select_for_update()
            .filter(pk__in=product_ids)
            .order_by('pk')
            .values_list('pk', flat=True)
        )


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
            # Hit the accent-normalized, trigram-indexed column on Customer.
            qs = qs.filter(customer__name_search__icontains=_normalize_name(q))
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        ctx['status_filter'] = self.request.GET.get('status', '')
        ctx['status_choices'] = Rental.Status.choices
        return ctx


# ── Add item by rental number ───────────────────────────────────────────────

class RentalAddItemEntryView(RentalAccessMixin, View):
    """Resolve a typed rental number and jump straight to adding items (R7.03).

    Lets staff reopen an existing contract by its number to append another piece
    without scrolling the list. Lands on the edit screen with the item section
    expanded (``?add=1``).
    """

    def get(self, request, *args, **kwargs):
        raw = (request.GET.get('number') or '').strip().lstrip('#')
        if not raw.isdigit():
            messages.error(request, 'Informe um número de locação válido.')
            return redirect('rentals:list')
        rental = Rental.objects.filter(number=int(raw)).only('pk', 'number', 'status').first()
        if not rental:
            messages.error(request, f'Locação #{raw} não encontrada.')
            return redirect('rentals:list')
        if rental.status in (Rental.Status.CANCELLED, Rental.Status.RETURNED):
            messages.error(
                request,
                f'A locação #{rental.number} está '
                f'{rental.get_status_display().lower()} e não aceita novos itens.',
            )
            return redirect(rental.get_absolute_url())
        return redirect(f"{reverse('rentals:update', args=[rental.pk])}?add=1")


# ── Detail ────────────────────────────────────────────────────────────────────

class RentalDetailView(RentalAccessMixin, DetailView):
    model = Rental
    template_name = 'rentals/rental_detail.html'
    context_object_name = 'rental'

    def get_queryset(self):
        items = RentalItem.objects.select_related('product__category').defer('proof_photo')
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

        items_total = sum(
            (
                item_form.cleaned_data.get('value') or Decimal('0')
                for item_form in items.forms
                if item_form.cleaned_data
                and not item_form.cleaned_data.get('DELETE')
                and item_form.cleaned_data.get('product')
            ),
            Decimal('0'),
        )
        installment_count = form.cleaned_data.get('installment_count') or 0
        first_due_date = form.cleaned_data.get('first_due_date')
        dp_amount = form.cleaned_data.get('down_payment_amount') or Decimal('0')
        dp_method = form.cleaned_data.get('down_payment_method')
        dp_date = form.cleaned_data.get('down_payment_date')
        cash_discount = form.cleaned_data.get('cash_discount')
        effective_total, _ = Rental.compute_cash_discount(
            items_total,
            applied=cash_discount,
            percent=form.cleaned_data.get('cash_discount_percent'),
            amount=form.cleaned_data.get('cash_discount_amount'),
        )
        remaining = effective_total - dp_amount

        if dp_amount > effective_total:
            form.add_error(
                'down_payment_amount',
                'O valor da entrada não pode superar o total da locação.',
            )
        if remaining > 0 and dp_amount > 0 and not installment_count:
            form.add_error(
                'installment_count',
                'Informe ao menos uma parcela futura para o saldo restante.',
            )
        if remaining > 0 and dp_amount > 0 and not first_due_date:
            form.add_error(
                'first_due_date',
                'Informe a data do próximo pagamento.',
            )
        if form.errors:
            return self.form_invalid(form)

        try:
            with transaction.atomic():
                lock_item_products(items)
                conflicts = check_item_availability(
                    items,
                    form.cleaned_data.get('pickup_date'),
                    form.cleaned_data.get('return_date'),
                )
                if conflicts:
                    for message in conflicts:
                        form.add_error(None, message)
                    messages.error(
                        self.request,
                        'Há peças já alocadas para o período. Revise os itens informados.',
                    )
                    return self.form_invalid(form)

                rental = form.save(commit=False)
                rental.number = Company.next_rental_number()
                rental.save()
                items.instance = rental
                items.save()
                rental.recalculate_total()

                if installment_count or dp_amount > 0:
                    create_rental_payment_plan(
                        rental,
                        installments=installment_count,
                        first_due_date=first_due_date,
                        down_payment_amount=dp_amount,
                        down_payment_method=dp_method,
                        down_payment_date=dp_date,
                        user=self.request.user,
                    )
        except PaymentPlanError as exc:
            field = exc.field if exc.field in form.fields else None
            form.add_error(field, str(exc))
            return self.form_invalid(form)

        self.object = rental
        messages.success(self.request, f'Locação #{rental.number} criada com sucesso.')
        if self.request.POST.get('save_and_print') == '1':
            return HttpResponseRedirect(f"{reverse('rentals:detail', args=[rental.pk])}?print=1")
        return HttpResponseRedirect(self.get_success_url())


# ── Update ────────────────────────────────────────────────────────────────────

class RentalUpdateView(RentalAccessMixin, UpdateView):
    """Edit a rental while preserving paid commercial terms (R7.09)."""

    model = Rental
    form_class = RentalForm
    template_name = 'rentals/rental_form.html'

    protected_field_names = (
        'customer',
        'pickup_date',
        'return_date',
    )

    def get_object(self, queryset=None):
        rental = super().get_object(queryset)
        if rental.status == Rental.Status.CANCELLED:
            messages.error(self.request, 'Não é possível editar uma locação cancelada.')
            raise Http404
        return rental

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # Once any payment exists, altering the commercial terms makes the
        # contract, receivables and payment history disagree.  Disabled Django
        # fields also ignore forged POST values, keeping this rule server-side.
        if self.object.receivables.filter(payments__isnull=False).exists():
            for field_name in self.protected_field_names:
                form.fields[field_name].disabled = True
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        has_payments = self.object.receivables.filter(payments__isnull=False).exists()
        context['has_payments'] = has_payments
        context['header_locked'] = has_payments
        if 'items' not in context:
            if self.request.POST:
                context['items'] = RentalItemEditFormSet(
                    self.request.POST,
                    self.request.FILES,
                    instance=self.object,
                )
            else:
                context['items'] = RentalItemEditFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        items = context['items']

        if not items.is_valid():
            return self.form_invalid(form)

        with transaction.atomic():
            locked_rental = Rental.objects.select_for_update().get(pk=self.object.pk)
            if locked_rental.status == Rental.Status.CANCELLED:
                form.add_error(None, 'A locação foi cancelada e não pode mais ser editada.')
                return self.form_invalid(form)

            list(
                RentalItem.objects.select_for_update()
                .filter(rental=locked_rental)
                .order_by('pk')
                .values_list('pk', flat=True)
            )
            lock_item_products(items)
            conflicts = check_item_availability(
                items,
                form.cleaned_data.get('pickup_date'),
                form.cleaned_data.get('return_date'),
                exclude_rental_id=locked_rental.pk,
            )
            if conflicts:
                for message in conflicts:
                    form.add_error(None, message)
                messages.error(
                    self.request,
                    'Há peças já alocadas para o período. Revise os itens informados.',
                )
                return self.form_invalid(form)

            has_payments = locked_rental.receivables.filter(
                payments__isnull=False,
            ).exists()
            rental = form.save(commit=False)
            if has_payments:
                for field_name in self.protected_field_names:
                    setattr(rental, field_name, getattr(locked_rental, field_name))
            rental.total_value = locked_rental.total_value
            rental.save()
            old_total = locked_rental.total_value
            items.instance = rental
            items.save()
            rental.recalculate_total()

        if has_payments and rental.total_value != old_total:
            messages.warning(
                self.request,
                'O total da locação foi alterado. Revise ou gere novamente as parcelas futuras '
                'na tela de Recebimentos desta locação.',
            )
        else:
            messages.success(self.request, f'Locação #{rental.number} atualizada.')
        if self.request.POST.get('save_and_print') == '1':
            return HttpResponseRedirect(f"{reverse('rentals:detail', args=[rental.pk])}?print=1")
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
        company = Company.load()
        ctx['cancellation_penalty_rate'] = company.cancellation_penalty_rate
        ctx['cancellation_penalty_amount'] = compute_cancellation_penalty(
            self.rental,
            company=company,
        )
        return ctx

    def form_valid(self, form):
        with transaction.atomic():
            rental = Rental.objects.select_for_update().get(pk=self.rental.pk)
            if rental.status in (Rental.Status.CANCELLED, Rental.Status.RETURNED):
                messages.error(self.request, 'Esta locação não pode ser cancelada.')
                return redirect(rental.get_absolute_url())
            rental.status = Rental.Status.CANCELLED
            rental.cancelled_reason = form.cleaned_data['reason']
            rental.cancelled_at = timezone.now()
            rental.cancelled_by = self.request.user
            rental.save(update_fields=[
                'status', 'cancelled_reason', 'cancelled_at', 'cancelled_by', 'updated_at',
            ])
            penalty = apply_cancellation_penalty(
                rental,
                user=self.request.user,
                due_date=timezone.localdate(),
            )
            AuditLog.objects.create(
                user=self.request.user,
                action='rental_cancel',
                model_name='Rental',
                object_id=str(rental.pk),
                object_repr=f'Locação #{rental.number}',
                reason=form.cleaned_data['reason'],
            )
        self.rental = rental
        messages.success(
            self.request,
            f'Locação #{rental.number} cancelada. Penalidade aplicada: '
            f'{penalty["rate"]}% (R$ {penalty["amount"]:.2f}).',
        )
        if penalty['paid_exceeds_amount']:
            messages.warning(
                self.request,
                'Os recebimentos já registrados superam a penalidade calculada; '
                'nenhum estorno foi feito automaticamente.',
            )
        return redirect(rental.get_absolute_url())


# ── Delete ────────────────────────────────────────────────────────────────────

class RentalDeleteView(RentalAccessMixin, ActionRequiredMixin, View):
    """Physically delete a rental only when no movement or payment exists (R7.11)."""

    action_key = 'rentals.delete'

    def get(self, request, *args, **kwargs):
        rental = get_object_or_404(Rental, pk=kwargs['pk'])
        return self._render_confirm(request, rental)

    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            rental = get_object_or_404(
                Rental.objects.select_for_update(),
                pk=kwargs['pk'],
            )
            has_pickup = hasattr(rental, 'pickup') and rental.pickup is not None
            has_return = (
                hasattr(rental, 'return_record')
                and rental.return_record is not None
            )
            has_payments = rental.receivables.filter(
                payments__isnull=False,
            ).exists()

            if has_pickup or has_return or has_payments:
                messages.error(
                    request,
                    'Não é possível excluir esta locação pois já possui retirada, devolução ou '
                    'pagamento registrados. Use o cancelamento.',
                )
                return redirect(rental.get_absolute_url())

            if rental.status == Rental.Status.CANCELLED:
                number = rental.number
                AuditLog.objects.create(
                    user=request.user,
                    action='rental_delete',
                    model_name='Rental',
                    object_id=str(rental.pk),
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

CONTRACT_VERSION = 'v3'


class RentalContractView(RentalAccessMixin, TemplateView):
    """Print-friendly rental contract (R7.07/R7.08)."""

    template_name = 'rentals/rental_contract.html'

    def get(self, request, *args, **kwargs):
        rental = get_object_or_404(
            Rental.objects.select_related('customer').prefetch_related(
                Prefetch(
                    'items',
                    queryset=RentalItem.objects.select_related('product__category').defer('proof_photo'),
                )
            ),
            pk=kwargs['pk'],
        )
        # R7.08 — stamp first print and keep the rendered layout version auditable.
        if not rental.contract_printed_at or rental.contract_version != CONTRACT_VERSION:
            printed_at = timezone.now()
            Rental.objects.filter(pk=rental.pk).update(
                contract_version=CONTRACT_VERSION,
                contract_printed_at=printed_at,
            )
            rental.contract_version = CONTRACT_VERSION
            rental.contract_printed_at = printed_at

        company = Company.load()
        items = list(rental.items.all())
        items_have_wearer = any(item.wearer_name for item in items)
        receivables = list(rental.receivables.order_by('due_date', 'pk'))
        payment_totals = {
            'amount': sum((receivable.amount for receivable in receivables), Decimal('0')),
            'paid': sum((receivable.paid_amount for receivable in receivables), Decimal('0')),
            'balance': sum((receivable.balance for receivable in receivables), Decimal('0')),
        }
        return self.render_to_response(self.get_context_data(
            rental=rental,
            company=company,
            items=items,
            items_have_wearer=items_have_wearer,
            receivables=receivables,
            payment_totals=payment_totals,
            contract_version=CONTRACT_VERSION,
            copy_labels=['1ª via — Locatário', '2ª via — Empresa'],
        ))
