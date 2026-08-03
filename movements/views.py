from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, ListView, TemplateView

from catalog.availability import find_upcoming_pickups
from company.models import Company
from core.mixins import ModuleAccessMixin
from core.ui import parse_br_date
from customers.models import _normalize_name
from rentals.models import Rental, RentalItem

from .forms import PickupForm, ReturnForm
from .models import Pickup, Return
from .services import compute_days_late, compute_penalty


class MovementsAccessMixin(ModuleAccessMixin):
    module_key = 'movements'


def _format_brl(value):
    formatted = f'{Decimal(value):,.2f}'.replace(',', '_')
    return formatted.replace('.', ',').replace('_', '.')


def _has_invalid_date_filter(request):
    return any(
        request.GET.get(key, '').strip() and not parse_br_date(request.GET.get(key))
        for key in ('date_from', 'date_to')
    )


class PickupCreateView(MovementsAccessMixin, CreateView):
    """Register the pickup of a rental's items (RF-17)."""

    form_class = PickupForm
    template_name = 'movements/pickup_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        if hasattr(self.rental, 'pickup'):
            messages.info(request, 'Esta locação já teve a retirada registrada.')
            return redirect('rentals:detail', pk=self.rental.pk)
        if self.rental.status != Rental.Status.PENDING:
            messages.error(
                request,
                'A retirada só pode ser registrada em uma locação pendente.',
            )
            return redirect('rentals:detail', pk=self.rental.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['rental'] = self.rental
        return context

    def form_valid(self, form):
        with transaction.atomic():
            rental = Rental.objects.select_for_update().get(pk=self.rental.pk)
            if (
                rental.status != Rental.Status.PENDING
                or Pickup.objects.filter(rental=rental).exists()
            ):
                messages.info(
                    self.request,
                    'Esta locação não está mais disponível para registrar retirada.',
                )
                return redirect('rentals:detail', pk=rental.pk)
            form.instance.rental = rental
            self.object = form.save()
        self.rental = rental
        messages.success(self.request, 'Retirada registrada com sucesso.')
        return redirect('rentals:detail', pk=rental.pk)


class ReturnCreateView(MovementsAccessMixin, CreateView):
    """Register the return of a rental, computing late days and penalty (RF-18)."""

    form_class = ReturnForm
    template_name = 'movements/return_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['rental'] = self.rental
        return kwargs

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        if hasattr(self.rental, 'return_record'):
            messages.info(request, 'Esta locação já teve a devolução registrada.')
            return redirect('rentals:detail', pk=self.rental.pk)
        if (
            self.rental.status != Rental.Status.PICKED_UP
            or not hasattr(self.rental, 'pickup')
        ):
            messages.error(
                request,
                'A devolução exige uma retirada registrada e ainda em aberto.',
            )
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
        days_late_today = compute_days_late(self.rental.return_date, timezone.localdate())
        company = Company.load()
        if days_late_today > company.late_return_max_days:
            from billing.services import compute_loss_penalty
            possible_penalty = sum(
                (
                    compute_loss_penalty(item.value, company=company)
                    for item in self.rental.items.all()
                ),
                Decimal('0'),
            )
        else:
            possible_penalty = compute_penalty(
                self.rental,
                days_late_today,
                company=company,
            )
        ctx['can_receive_on_return'] = (
            bool(open_receivables)
            or possible_penalty > 0
        )
        return ctx

    def form_valid(self, form):
        from billing.models import Receivable
        from billing.services import (
            compute_damage_penalty,
            compute_loss_penalty,
            create_penalty_receivable,
            register_payment,
        )

        return_date = form.cleaned_data['return_date']
        payment_amount = form.cleaned_data.get('payment_amount') or Decimal('0')
        payment_method = form.cleaned_data.get('payment_method', '')
        payment_date = form.cleaned_data.get('payment_date') or timezone.localdate()

        payment_info = ''
        with transaction.atomic():
            # Serialize returns for the same rental and lock current balances
            # before deciding whether an optional payment can be allocated.
            rental = Rental.objects.select_for_update().get(pk=self.rental.pk)
            if Return.objects.filter(rental=rental).exists():
                messages.info(self.request, 'Esta locação já teve a devolução registrada.')
                return redirect('rentals:detail', pk=rental.pk)
            if (
                rental.status != Rental.Status.PICKED_UP
                or not Pickup.objects.filter(rental=rental).exists()
            ):
                messages.error(
                    self.request,
                    'A locação não está mais disponível para registrar devolução.',
                )
                return redirect('rentals:detail', pk=rental.pk)

            company = Company.load()
            rental_items = {
                item.pk: item
                for item in RentalItem.objects.select_for_update()
                .filter(rental=rental)
                .select_related('product__category')
            }
            damaged_items = [
                rental_items[item.pk]
                for item in form.cleaned_data['damaged_items']
                if item.pk in rental_items
            ]
            damage_amount = sum(
                (
                    compute_damage_penalty(item.value, company=company)
                    for item in damaged_items
                ),
                Decimal('0'),
            )
            days_late = compute_days_late(rental.return_date, return_date)
            lost_items = []
            loss_amount = Decimal('0')
            if days_late > company.late_return_max_days:
                lost_items = list(rental_items.values())
                loss_amount = sum(
                    (
                        compute_loss_penalty(item.value, company=company)
                        for item in lost_items
                    ),
                    Decimal('0'),
                )
                penalty_applied = Decimal('0')
            else:
                penalty_applied = compute_penalty(
                    rental,
                    days_late,
                    company=company,
                )
            open_receivables = list(
                Receivable.objects.select_for_update()
                .filter(rental=rental, balance__gt=0)
                .order_by('due_date')
            )
            open_balance = sum((receivable.balance for receivable in open_receivables), Decimal('0'))
            available_balance = open_balance + penalty_applied + damage_amount + loss_amount
            if payment_amount > available_balance:
                form.add_error(
                    'payment_amount',
                    'O valor recebido é maior que o saldo em aberto '
                    f'(R$ {_format_brl(available_balance)}).',
                )
                return self.form_invalid(form)

            # 1. Create Return object
            return_obj = form.save(commit=False)
            return_obj.rental = rental
            return_obj.days_late = days_late
            return_obj.penalty_applied = penalty_applied
            return_obj.damage_amount = damage_amount if damaged_items else None
            return_obj.damage_rate = company.damage_penalty_rate if damaged_items else None
            return_obj.loss_amount = loss_amount
            return_obj.loss_rate = company.loss_penalty_rate if lost_items else None
            return_obj.save()
            if damaged_items:
                return_obj.damaged_items.set(damaged_items)
            if lost_items:
                return_obj.lost_items.set(lost_items)
            self.object = return_obj

            # 2. Create all calculated receivables before allocating an optional payment.
            if days_late > 0 and penalty_applied > 0:
                penalty_receivable = create_penalty_receivable(
                    rental,
                    amount=penalty_applied,
                    due_date=return_obj.return_date,
                    kind='late_return',
                    user=self.request.user,
                    details=(
                        f'{days_late} dia(s), percentual diário '
                        f'{company.late_return_daily_rate}%'
                    ),
                )
                if penalty_receivable:
                    open_receivables.append(penalty_receivable)
            if damaged_items and damage_amount > 0:
                damage_receivable = create_penalty_receivable(
                    rental,
                    amount=damage_amount,
                    due_date=return_obj.return_date,
                    kind='damage',
                    user=self.request.user,
                    details=(
                        f'{company.damage_penalty_rate}% sobre: '
                        + ', '.join(item.product_reference for item in damaged_items)
                    ),
                )
                if damage_receivable:
                    open_receivables.append(damage_receivable)
            if lost_items and loss_amount > 0:
                loss_receivable = create_penalty_receivable(
                    rental,
                    amount=loss_amount,
                    due_date=return_obj.return_date,
                    kind='loss',
                    user=self.request.user,
                    details=(
                        f'{company.loss_penalty_rate}% sobre: '
                        + ', '.join(item.product_reference for item in lost_items)
                    ),
                )
                if loss_receivable:
                    open_receivables.append(loss_receivable)

            # 3. Handle optional payment (R10.05)
            if payment_amount > Decimal('0') and payment_method:
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
                payment_info = (
                    f' Recebimento de R$ {_format_brl(payment_amount)} registrado.'
                )

        msg = (
            f'Devolução registrada. Dias de atraso: {days_late}; '
            f'multa: R$ {_format_brl(return_obj.penalty_applied)}.'
        )
        if return_obj.damage_amount:
            msg += f' Danos: R$ {_format_brl(return_obj.damage_amount)}.'
        if return_obj.loss_amount:
            msg += f' Perda/não devolução: R$ {_format_brl(return_obj.loss_amount)}.'
        msg += payment_info
        messages.success(self.request, msg)
        return redirect('rentals:detail', pk=rental.pk)


class PickupListView(MovementsAccessMixin, ListView):
    """Rentals pending pickup, filterable by date/customer/product (R10.01/R10.02)."""

    model = Rental
    template_name = 'movements/pickup_list.html'
    context_object_name = 'rentals'
    paginate_by = 30

    def get_queryset(self):
        qs = (
            Rental.pending_pickup_queryset()
            .select_related('customer')
            .prefetch_related('items__product__category')
            .order_by('pickup_date', 'number')
        )
        date_from = parse_br_date(self.request.GET.get('date_from'))
        date_to = parse_br_date(self.request.GET.get('date_to'))
        customer_q = self.request.GET.get('customer', '').strip()
        product_q = self.request.GET.get('product', '').strip()
        if _has_invalid_date_filter(self.request):
            messages.error(self.request, 'Informe as datas no formato dd/mm/aaaa.')
            return qs.none()
        if date_from:
            qs = qs.filter(pickup_date__gte=date_from)
        if date_to:
            qs = qs.filter(pickup_date__lte=date_to)
        if customer_q:
            qs = qs.filter(customer__name_search__icontains=_normalize_name(customer_q))
        if product_q:
            qs = qs.filter(
                Q(items__product__category__prefix__icontains=product_q)
                | Q(items__product__description_search__icontains=_normalize_name(product_q))
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        date_from = parse_br_date(self.request.GET.get('date_from'))
        date_to = parse_br_date(self.request.GET.get('date_to'))
        ctx.update({
            'date_from': date_from.isoformat() if date_from else '',
            'date_to': date_to.isoformat() if date_to else '',
            'customer_q': self.request.GET.get('customer', ''),
            'product_q': self.request.GET.get('product', ''),
            'today': timezone.localdate(),
        })
        return ctx


class ReturnListView(MovementsAccessMixin, ListView):
    """Rentals already picked up, filterable by dates/customer/product (R10.03)."""

    model = Rental
    template_name = 'movements/return_list.html'
    context_object_name = 'rentals'
    paginate_by = 30
    URGENT_PICKUP_WINDOW_DAYS = 10

    def get_queryset(self):
        qs = (
            Rental.objects.filter(status=Rental.Status.PICKED_UP)
            .select_related('customer')
            .prefetch_related('items__product__category', 'pickup', 'return_record')
            .order_by('return_date', 'number')
        )
        date_from = parse_br_date(self.request.GET.get('date_from'))
        date_to = parse_br_date(self.request.GET.get('date_to'))
        customer_q = self.request.GET.get('customer', '').strip()
        product_q = self.request.GET.get('product', '').strip()
        if _has_invalid_date_filter(self.request):
            messages.error(self.request, 'Informe as datas no formato dd/mm/aaaa.')
            return qs.none()
        if date_from:
            qs = qs.filter(pickup__pickup_date__gte=date_from)
        if date_to:
            qs = qs.filter(pickup__pickup_date__lte=date_to)
        if customer_q:
            qs = qs.filter(customer__name_search__icontains=_normalize_name(customer_q))
        if product_q:
            qs = qs.filter(
                Q(items__product__category__prefix__icontains=product_q)
                | Q(items__product__description_search__icontains=_normalize_name(product_q))
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        date_from = parse_br_date(self.request.GET.get('date_from'))
        date_to = parse_br_date(self.request.GET.get('date_to'))
        rentals = ctx[self.context_object_name]
        ctx['completed_returns'] = (
            Return.objects.select_related('rental__customer')
            .order_by('-return_date', '-pk')[:30]
        )
        product_ids = {item.product_id for rental in rentals for item in rental.items.all()}
        upcoming = find_upcoming_pickups(product_ids, today, within_days=self.URGENT_PICKUP_WINDOW_DAYS)
        for rental in rentals:
            rental.urgent_pickup = next(
                (upcoming[item.product_id] for item in rental.items.all() if item.product_id in upcoming),
                None,
            )
        ctx.update({
            'date_from': date_from.isoformat() if date_from else '',
            'date_to': date_to.isoformat() if date_to else '',
            'customer_q': self.request.GET.get('customer', ''),
            'product_q': self.request.GET.get('product', ''),
            'today': today,
        })
        return ctx


class OverdueListView(MovementsAccessMixin, TemplateView):
    """Picked-up rentals past their return date (R10.04)."""

    template_name = 'movements/overdue_list.html'

    paginate_by = 30

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        rentals = (
            Rental.objects.filter(status=Rental.Status.PICKED_UP, return_date__lt=today)
            .select_related('customer')
            .order_by('return_date', 'number')
        )
        paginator = Paginator(rentals, self.paginate_by)
        page_obj = paginator.get_page(self.request.GET.get('page'))
        # Annotate days late in Python (avoid DB func dependency)
        overdue = [
            {'rental': r, 'days_late': (today - r.return_date).days}
            for r in page_obj
        ]
        ctx['overdue'] = overdue
        ctx['page_obj'] = page_obj
        ctx['paginator'] = paginator
        ctx['is_paginated'] = page_obj.has_other_pages()
        ctx['today'] = today
        return ctx
