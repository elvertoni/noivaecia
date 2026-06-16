import re

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from core.mixins import ModuleAccessMixin, ActionRequiredMixin

from .forms import CustomerForm
from .models import Customer


def _fmt_cpf(digits):
    """Formata 11 dígitos no padrão 000.000.000-00."""
    return f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}'


def _digits(value):
    return re.sub(r'\D', '', value or '')


class CustomerListView(ModuleAccessMixin, ListView):
    """Paginated, searchable customer listing (RF-11)."""

    module_key = 'customers'
    model = Customer
    template_name = 'customers/customer_list.html'
    context_object_name = 'customers'
    paginate_by = 25

    # Colunas necessárias na lista — evita carregar text blobs de 18k linhas
    _LIST_FIELDS = ('pk', 'name', 'city', 'state', 'rg', 'cpf', 'phone_mobile',
                    'phone_home', 'is_active', 'legacy_id')

    def get_queryset(self):
        queryset = Customer.objects.only(*self._LIST_FIELDS).order_by('name')

        search = self.request.GET.get('q', '').strip()
        cpf_q = self.request.GET.get('cpf', '').strip()
        active = self.request.GET.get('active', '').strip()

        if search:
            digits = _digits(search)
            q_filter = (
                Q(name__icontains=search)
                | Q(phone_home__icontains=search)
                | Q(phone_mobile__icontains=search)
                | Q(phone_work__icontains=search)
                | Q(rg__icontains=search)
                | Q(cpf__icontains=search)
            )
            if digits:
                q_filter |= (
                    Q(cpf_digits__icontains=digits)
                    | Q(rg_digits__icontains=digits)
                    | Q(phone_mobile_digits__icontains=digits)
                )
                # Aceita CPF digitado sem formatação (11 dígitos)
                if len(digits) == 11:
                    q_filter |= Q(cpf=_fmt_cpf(digits))
                elif len(digits) <= 10:
                    q_filter |= Q(legacy_id=int(digits))
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


class CustomerDeleteView(ModuleAccessMixin, ActionRequiredMixin, DeleteView):
    module_key = 'customers'
    action_key = 'customers.delete'
    model = Customer
    template_name = 'customers/customer_confirm_delete.html'
    success_url = reverse_lazy('customers:list')

    def form_valid(self, form):
        customer = self.get_object()
        has_rentals = customer.rentals.exists()
        has_receivables = False
        if not has_rentals:
            from billing.models import Receivable
            has_receivables = Receivable.objects.filter(rental__customer=customer).exists()
        if has_rentals or has_receivables:
            messages.error(
                self.request,
                'Cliente possui histórico de locações ou recebimentos e não pode ser excluído. '
                'Use a inativação.',
            )
            return redirect('customers:detail', pk=customer.pk)
        from core.models import AuditLog
        AuditLog.objects.create(
            user=self.request.user,
            action='customer_delete',
            model_name='Customer',
            object_id=str(customer.pk),
            object_repr=str(customer),
            reason='Exclusão física de cliente.',
        )
        messages.success(self.request, 'Cliente excluído com sucesso.')
        return super().form_valid(form)


class CustomerDeactivateView(ModuleAccessMixin, View):
    module_key = 'customers'

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        customer.is_active = not customer.is_active
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

    def get_context_data(self, **kwargs):
        from decimal import Decimal
        from django.db.models import Sum
        from billing.models import Payment, Receivable
        from rentals.models import Rental, RentalItem

        ctx = super().get_context_data(**kwargs)
        customer = self.object

        # Filters (R9.03)
        date_from = self.request.GET.get('date_from', '').strip()
        date_to = self.request.GET.get('date_to', '').strip()
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
            rentals_qs = rentals_qs.filter(
                Q(items__product__description__icontains=product_q)
                | Q(items__product__category__prefix__icontains=product_q)
            ).distinct()

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
            'rentals': rentals_qs,
            'receivables': receivables_qs,
            'payments': payments_qs,
            'total_rented': total_rented,
            'total_amount': rec_totals['total_amount'] or Decimal('0'),
            'total_paid': rec_totals['total_paid'] or Decimal('0'),
            'total_balance': rec_totals['total_balance'] or Decimal('0'),
            # filter echoes for template
            'date_from': date_from,
            'date_to': date_to,
            'rental_status': rental_status,
            'financial_status': financial_status,
            'product_q': product_q,
            'rental_status_choices': Rental.Status.choices,
        })
        return ctx


class CustomerSearchView(View):
    """JSON quick-search for customer picker in rental form (R7.02).

    Returns up to 15 matches for query ``q`` across name, CPF, RG, phones and legacy_id.
    Requires any authenticated user (rental module access checked client-side).
    """

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        q = request.GET.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'results': []})
        digits = _digits(q)
        q_filter = (
            Q(name__icontains=q)
            | Q(cpf__icontains=q)
            | Q(rg__icontains=q)
            | Q(phone_home__icontains=q)
            | Q(phone_mobile__icontains=q)
            | Q(phone_work__icontains=q)
        )
        qs = Customer.objects.all()
        if digits:
            q_filter |= (
                Q(cpf_digits__icontains=digits)
                | Q(rg_digits__icontains=digits)
                | Q(phone_mobile_digits__icontains=digits)
            )
        qs = qs.filter(q_filter).values('id', 'name', 'cpf', 'rg', 'city')[:15]
        results = [
            {
                'id': c['id'],
                'text': c['name'],
                'sub': f"CPF {c['cpf'] or '—'} · RG {c['rg'] or '—'} · {c['city'] or '—'}",
            }
            for c in qs
        ]
        return JsonResponse({'results': results})
