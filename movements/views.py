from datetime import date as date_cls
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, ListView, TemplateView

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
        self.rental.status = Rental.Status.PICKED_UP
        self.rental.save(update_fields=['status', 'updated_at'])
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
        from billing.models import Receivable
        ctx = super().get_context_data(**kwargs)
        ctx['rental'] = self.rental
        open_receivables = Receivable.objects.filter(
            rental=self.rental, balance__gt=0
        ).order_by('due_date')
        ctx['open_receivables'] = open_receivables
        ctx['total_open_balance'] = open_receivables.aggregate(s=Sum('balance'))['s'] or Decimal('0')
        return ctx

    def form_valid(self, form):
        from billing.models import Receivable
        from billing.services import register_payment

        # 1. Create Return object
        return_obj = form.save(commit=False)
        return_obj.rental = self.rental
        days_late = compute_days_late(self.rental.return_date, return_obj.return_date)
        return_obj.days_late = days_late
        penalty_applied = compute_penalty(self.rental, days_late)
        return_obj.penalty_applied = penalty_applied
        return_obj.save()
        self.object = return_obj

        # 2. Update rental status
        self.rental.status = Rental.Status.RETURNED
        self.rental.save(update_fields=['status', 'updated_at'])

        # 3. Create penalty receivable if applicable (R10.06)
        if days_late > 0 and penalty_applied > 0:
            Receivable.objects.create(
                rental=self.rental,
                due_date=return_obj.return_date,
                amount=return_obj.penalty_applied,
                legacy_notes='Multa de atraso na devolução',
            )

        # 4. Handle optional payment (R10.05)
        payment_amount = form.cleaned_data.get('payment_amount') or Decimal('0')
        payment_method = form.cleaned_data.get('payment_method', '')
        payment_date = form.cleaned_data.get('payment_date') or date_cls.today()

        payment_info = ''
        if payment_amount > Decimal('0') and payment_method:
            open_receivables = Receivable.objects.filter(
                rental=self.rental, balance__gt=0
            ).order_by('due_date')
            remaining = payment_amount
            for receivable in open_receivables:
                if remaining <= Decimal('0'):
                    break
                to_pay = min(remaining, receivable.balance)
                register_payment(
                    receivable=receivable,
                    amount=to_pay,
                    payment_date=payment_date,
                    method=payment_method,
                    user=self.request.user,
                )
                remaining -= to_pay
            payment_info = f' Pagamento de R$ {payment_amount} registrado.'

        msg = (
            f'Devolução registrada. Dias de atraso: {days_late}; '
            f'multa: R$ {return_obj.penalty_applied}.{payment_info}'
        )
        messages.success(self.request, msg)
        return redirect('rentals:detail', pk=self.rental.pk)


class PickupListView(MovementsAccessMixin, ListView):
    """Rentals pending pickup, filterable by date/customer/product (R10.01/R10.02)."""

    model = Rental
    template_name = 'movements/pickup_list.html'
    context_object_name = 'rentals'
    paginate_by = 30

    def get_queryset(self):
        qs = (
            Rental.objects.filter(status=Rental.Status.PENDING)
            .select_related('customer')
            .prefetch_related('items__product__category')
            .order_by('pickup_date', 'number')
        )
        date_from = self.request.GET.get('date_from', '').strip()
        date_to = self.request.GET.get('date_to', '').strip()
        customer_q = self.request.GET.get('customer', '').strip()
        product_q = self.request.GET.get('product', '').strip()
        if date_from:
            qs = qs.filter(pickup_date__gte=date_from)
        if date_to:
            qs = qs.filter(pickup_date__lte=date_to)
        if customer_q:
            qs = qs.filter(customer__name__icontains=customer_q)
        if product_q:
            qs = qs.filter(
                Q(items__product__category__prefix__icontains=product_q)
                | Q(items__product__description__icontains=product_q)
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
            'customer_q': self.request.GET.get('customer', ''),
            'product_q': self.request.GET.get('product', ''),
            'today': date_cls.today(),
        })
        return ctx


class ReturnListView(MovementsAccessMixin, ListView):
    """Rentals already picked up, filterable by dates/customer/product (R10.03)."""

    model = Rental
    template_name = 'movements/return_list.html'
    context_object_name = 'rentals'
    paginate_by = 30

    def get_queryset(self):
        qs = (
            Rental.objects.filter(status=Rental.Status.PICKED_UP)
            .select_related('customer')
            .prefetch_related('items__product__category', 'pickup', 'return_record')
            .order_by('return_date', 'number')
        )
        date_from = self.request.GET.get('date_from', '').strip()
        date_to = self.request.GET.get('date_to', '').strip()
        customer_q = self.request.GET.get('customer', '').strip()
        product_q = self.request.GET.get('product', '').strip()
        if date_from:
            qs = qs.filter(pickup__pickup_date__gte=date_from)
        if date_to:
            qs = qs.filter(pickup__pickup_date__lte=date_to)
        if customer_q:
            qs = qs.filter(customer__name__icontains=customer_q)
        if product_q:
            qs = qs.filter(
                Q(items__product__category__prefix__icontains=product_q)
                | Q(items__product__description__icontains=product_q)
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date_cls.today()
        ctx.update({
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
            'customer_q': self.request.GET.get('customer', ''),
            'product_q': self.request.GET.get('product', ''),
            'today': today,
        })
        return ctx


class OverdueListView(MovementsAccessMixin, TemplateView):
    """Picked-up rentals past their return date (R10.04)."""

    template_name = 'movements/overdue_list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date_cls.today()
        rentals = (
            Rental.objects.filter(status=Rental.Status.PICKED_UP, return_date__lt=today)
            .select_related('customer')
            .order_by('return_date', 'number')
        )
        # Annotate days late in Python (avoid DB func dependency)
        overdue = []
        for r in rentals:
            overdue.append({
                'rental': r,
                'days_late': (today - r.return_date).days,
            })
        ctx['overdue'] = overdue
        ctx['today'] = today
        return ctx
