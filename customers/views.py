import re

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from core.mixins import ModuleAccessMixin, ActionRequiredMixin
from core.ui import parse_br_date

from .forms import CustomerForm
from .models import Customer, _normalize_name


def _fmt_cpf(digits):
    """Formata 11 dígitos no padrão 000.000.000-00."""
    return f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}'


def _digits(value):
    return re.sub(r'\D', '', value or '')


def _warn_if_duplicate_cpf(request, customer):
    if not customer.cpf_digits:
        return

    duplicate = (
        Customer.objects.filter(cpf_digits=customer.cpf_digits, is_active=True)
        .exclude(pk=customer.pk)
        .order_by('name')
        .first()
    )
    if duplicate:
        messages.warning(
            request,
            f'Já existe outro cliente com este CPF: {duplicate.name}. '
            'Confira antes de continuar.',
        )


def _parse_history_date(value):
    """Accept ISO and Brazilian dates from the customer-history filter."""
    return parse_br_date(value)


class CustomerListView(ModuleAccessMixin, ListView):
    """Paginated, searchable customer listing (RF-11)."""

    module_key = 'customers'
    model = Customer
    template_name = 'customers/customer_list.html'
    context_object_name = 'customers'
    paginate_by = 25

    # Colunas necessárias na lista — evita carregar text blobs de 18k linhas
    _LIST_FIELDS = (
        'pk', 'name', 'city', 'state', 'rg', 'cpf', 'cnpj', 'phone_mobile',
        'phone_home', 'alternate_phone_contact', 'is_active', 'legacy_id',
    )

    def get_queryset(self):
        queryset = Customer.objects.only(*self._LIST_FIELDS).order_by('name')

        search = self.request.GET.get('q', '').strip()
        cpf_q = self.request.GET.get('cpf', '').strip()
        active = self.request.GET.get('active', '').strip()

        if search:
            digits = _digits(search)
            name_norm = _normalize_name(search)
            q_filter = (
                # name_search is accent-normalized and backed by a trigram GIN
                # index (customer_name_trgm_idx) — query it, not the raw name.
                Q(name_search__icontains=name_norm)
                | Q(alternate_phone_contact__icontains=search)
                | Q(phone_home__icontains=search)
                | Q(phone_mobile__icontains=search)
                | Q(phone_work__icontains=search)
                | Q(rg__icontains=search)
                | Q(cpf__icontains=search)
                | Q(cnpj__icontains=search)
            )
            if digits:
                q_filter |= (
                    Q(cpf_digits__icontains=digits)
                    | Q(cnpj_digits__icontains=digits)
                    | Q(rg_digits__icontains=digits)
                    | Q(phone_home_digits__icontains=digits)
                    | Q(phone_mobile_digits__icontains=digits)
                )
                # Aceita CPF digitado sem formatação (11 dígitos)
                if len(digits) == 11:
                    q_filter |= Q(cpf=_fmt_cpf(digits))
                elif len(digits) <= 10:
                    val = int(digits)
                    if val <= 2147483647:
                        q_filter |= Q(legacy_id=val)
            queryset = queryset.filter(q_filter)

        if cpf_q:
            cpf_digits = _digits(cpf_q)
            if cpf_digits:
                queryset = queryset.filter(cpf_digits__icontains=cpf_digits)
            else:
                queryset = queryset.filter(cpf__icontains=cpf_q)

        if active == '1':
            queryset = queryset.filter(is_active=True)
        elif active == '0':
            queryset = queryset.filter(is_active=False)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('q', '')
        context['cpf_search'] = self.request.GET.get('cpf', '')
        context['active_filter'] = self.request.GET.get('active', '')
        context['total_count'] = context['paginator'].count
        page_obj = context.get('page_obj')
        if page_obj is not None:
            # Bounded window instead of iterating every page (720+ at 18k rows).
            context['elided_pages'] = context['paginator'].get_elided_page_range(
                page_obj.number, on_each_side=2, on_ends=1
            )
        return context


class CustomerCreateView(ModuleAccessMixin, SuccessMessageMixin, CreateView):
    module_key = 'customers'
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'
    success_url = reverse_lazy('customers:list')
    success_message = 'Cliente cadastrado com sucesso.'

    def form_valid(self, form):
        response = super().form_valid(form)
        _warn_if_duplicate_cpf(self.request, self.object)
        return response


class CustomerUpdateView(ModuleAccessMixin, SuccessMessageMixin, UpdateView):
    module_key = 'customers'
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'
    success_url = reverse_lazy('customers:list')
    success_message = 'Cliente atualizado com sucesso.'

    def form_valid(self, form):
        response = super().form_valid(form)
        _warn_if_duplicate_cpf(self.request, self.object)
        return response


class CustomerDeleteView(ModuleAccessMixin, ActionRequiredMixin, DeleteView):
    module_key = 'customers'
    action_key = 'customers.delete'
    model = Customer
    template_name = 'customers/customer_confirm_delete.html'
    success_url = reverse_lazy('customers:list')

    def form_valid(self, form):
        customer = self.object
        has_protected_history = (
            customer.rentals.exists()
            or customer.payments.exists()
            or customer.whatsapp_messages.exists()
        )
        if has_protected_history:
            messages.error(
                self.request,
                'Cliente possui histórico de locações, recebimentos ou mensagens '
                'e não pode ser excluído. Use a inativação.',
            )
            return redirect('customers:detail', pk=customer.pk)
        from core.models import AuditLog

        try:
            with transaction.atomic():
                AuditLog.objects.create(
                    user=self.request.user,
                    action='customer_delete',
                    model_name='Customer',
                    object_id=str(customer.pk),
                    object_repr=str(customer),
                    reason='Exclusão física de cliente.',
                )
                response = super().form_valid(form)
        except ProtectedError:
            messages.error(
                self.request,
                'Cliente possui histórico protegido e não pode ser excluído. '
                'Use a inativação.',
            )
            return redirect('customers:detail', pk=customer.pk)

        messages.success(self.request, 'Cliente excluído com sucesso.')
        return response


class CustomerDeactivateView(ModuleAccessMixin, ActionRequiredMixin, View):
    module_key = 'customers'
    action_key = 'customers.deactivate'

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        requested_state = request.POST.get('is_active')
        if requested_state not in ('0', '1'):
            messages.error(request, 'Informe se o cliente deve ficar ativo ou inativo.')
            return redirect('customers:detail', pk=pk)

        customer.is_active = requested_state == '1'
        customer.save(update_fields=['is_active', 'updated_at'])
        verb = 'ativado' if customer.is_active else 'inativado'
        messages.success(request, f'Cliente {verb} com sucesso.')
        return redirect('customers:detail', pk=pk)


class CustomerDetailView(ModuleAccessMixin, DetailView):
    """Customer history page (R9.02-R9.05)."""

    module_key = 'customers'
    model = Customer
    template_name = 'customers/customer_detail.html'
    context_object_name = 'customer'
    rentals_paginate_by = 25

    def get_context_data(self, **kwargs):
        from decimal import Decimal
        from django.db.models import Sum
        from billing.models import Payment, Receivable
        from rentals.models import Rental, RentalItem

        ctx = super().get_context_data(**kwargs)
        customer = self.object

        # Filters (R9.03)
        date_from_raw = self.request.GET.get('date_from', '').strip()
        date_to_raw = self.request.GET.get('date_to', '').strip()
        date_from = _parse_history_date(date_from_raw)
        date_to = _parse_history_date(date_to_raw)
        filter_errors = []
        if date_from_raw and not date_from:
            filter_errors.append('Informe a data inicial no formato dd/mm/aaaa.')
        if date_to_raw and not date_to:
            filter_errors.append('Informe a data final no formato dd/mm/aaaa.')
        if date_from and date_to and date_from > date_to:
            filter_errors.append('A data inicial não pode ser posterior à data final.')
            date_from = None
            date_to = None
        rental_status = self.request.GET.get('rental_status', '').strip()
        financial_status = self.request.GET.get('financial_status', '').strip()
        product_q = self.request.GET.get('product', '').strip()

        # Rentals — optimized (R9.05)
        rental_items = RentalItem.objects.select_related('product__category').defer('proof_photo')
        rentals_qs = (
            Rental.objects.filter(customer=customer)
            .select_related('pickup', 'return_record')
            .prefetch_related(Prefetch('items', queryset=rental_items))
            .order_by('-number')
        )
        if date_from:
            rentals_qs = rentals_qs.filter(pickup_date__gte=date_from)
        if date_to:
            rentals_qs = rentals_qs.filter(pickup_date__lte=date_to)
        if rental_status:
            rentals_qs = rentals_qs.filter(status=rental_status)
        if product_q:
            # Search the frozen snapshot as well as the live catalogue row: a
            # code can be retired and reused, and after that the row carries a
            # different piece.  Matching only the live description would hide
            # the rental under the name it was actually contracted with.
            rentals_qs = rentals_qs.filter(
                Q(items__product__description__icontains=product_q)
                | Q(items__product__category__prefix__icontains=product_q)
                | Q(items__product_description_snapshot__icontains=product_q)
                | Q(items__product_prefix_snapshot__icontains=product_q)
            ).distinct()
        rentals_paginator = Paginator(rentals_qs, self.rentals_paginate_by)
        rentals_page = rentals_paginator.get_page(self.request.GET.get('rental_page'))
        rentals_count = rentals_paginator.count

        rental_query = self.request.GET.copy()
        rental_query.pop('rental_page', None)
        rental_querystring = rental_query.urlencode()
        if rental_querystring:
            rental_querystring += '&'

        # Receivables
        receivables_qs = (
            Receivable.objects.filter(rental__customer=customer)
            .select_related('rental')
            .order_by('due_date')
        )
        if financial_status == 'open':
            receivables_qs = receivables_qs.filter(balance__gt=0)
        elif financial_status == 'paid':
            receivables_qs = receivables_qs.filter(balance__lte=0)
        receivables_qs = receivables_qs[:200]

        # Recent payments (R9.04)
        payments_qs = (
            Payment.objects.filter(customer=customer)
            .select_related('receivable', 'rental')
            .order_by('-payment_date')[:50]
        )

        # Financial summary (R9.04)
        rec_totals = Receivable.objects.filter(rental__customer=customer).aggregate(
            total_amount=Sum('amount'),
            total_paid=Sum('paid_amount'),
            total_balance=Sum('balance'),
        )
        total_rented = (
            Rental.objects.filter(customer=customer)
            .exclude(status=Rental.Status.CANCELLED)
            .aggregate(total=Sum('total_value'))['total'] or Decimal('0')
        )

        ctx.update({
            'rentals': rentals_page.object_list,
            'rentals_count': rentals_count,
            'rentals_page_obj': rentals_page,
            'rentals_is_paginated': rentals_page.has_other_pages(),
            'rental_page_querystring': rental_querystring,
            'receivables': receivables_qs,
            'payments': payments_qs,
            'total_rented': total_rented,
            'total_amount': rec_totals['total_amount'] or Decimal('0'),
            'total_paid': rec_totals['total_paid'] or Decimal('0'),
            'total_balance': rec_totals['total_balance'] or Decimal('0'),
            # filter echoes for template
            'date_from': date_from.isoformat() if date_from else date_from_raw,
            'date_to': date_to.isoformat() if date_to else date_to_raw,
            'filter_errors': filter_errors,
            'rental_status': rental_status,
            'financial_status': financial_status,
            'product_q': product_q,
            'rental_status_choices': Rental.Status.choices,
        })
        return ctx


class CustomerSearchView(ModuleAccessMixin, View):
    """JSON quick-search for customer picker in rental form (R7.02).

    Returns up to 15 matches for query ``q`` across name, CPF, RG, phones and legacy_id.
    Requires access to either Customers or Rentals because the endpoint returns
    personally identifiable data and is used by the rental customer picker.
    """

    module_key = None

    def has_module_permission(self):
        user = self.request.user
        return user.has_module('customers') or user.has_module('rentals')

    def get(self, request, *args, **kwargs):
        q = request.GET.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'results': []})
        digits = _digits(q)
        name_norm = _normalize_name(q)
        q_filter = (
            Q(name_search__icontains=name_norm)
            | Q(alternate_phone_contact__icontains=q)
            | Q(cpf__icontains=q)
            | Q(cnpj__icontains=q)
            | Q(rg__icontains=q)
            | Q(phone_home__icontains=q)
            | Q(phone_mobile__icontains=q)
            | Q(phone_work__icontains=q)
        )
        # Merged/deactivated duplicates must not resurface in the rental picker.
        qs = Customer.objects.filter(is_active=True)
        if digits:
            q_filter |= (
                Q(cpf_digits__icontains=digits)
                | Q(cnpj_digits__icontains=digits)
                | Q(rg_digits__icontains=digits)
                | Q(phone_home_digits__icontains=digits)
                | Q(phone_mobile_digits__icontains=digits)
            )
        qs = qs.filter(q_filter).values('id', 'name', 'cpf', 'cnpj', 'rg', 'city')[:15]
        results = [
            {
                'id': c['id'],
                'text': c['name'],
                'sub': (
                    f"CNPJ {c['cnpj']} · {c['city'] or '—'}"
                    if c['cnpj']
                    else f"CPF {c['cpf'] or '—'} · RG {c['rg'] or '—'} · {c['city'] or '—'}"
                ),
            }
            for c in qs
        ]
        return JsonResponse({'results': results})
