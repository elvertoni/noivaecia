import csv
from datetime import date as date_cls
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import FormView, ListView, TemplateView, View

from core.mixins import ModuleAccessMixin, ActionRequiredMixin
from core.ui import parse_br_date
from company.models import Company
from customers.models import Customer, _normalize_name
from rentals.models import Rental

from .forms import (
    GenerateReceivablesForm,
    ManualMovementForm,
    MultiPayForm,
    PaymentForm,
    ReceivablePayForm,
    ReversalForm,
)
from .models import CashAccount, FinancialMovement, Payment, Receivable
from .services import (
    financial_kpis,
    generate_for_rental,
    interest_breakdown,
    reconcile_financial,
    register_payment,
    reverse_payment,
    total_with_interest,
)


class BillingAccessMixin(ModuleAccessMixin):
    module_key = 'billing'


def _filters_for_display(request):
    """Keep date inputs valid after filters arrive in Brazilian notation."""
    filters = request.GET.copy()
    for key in ('date_from', 'date_to'):
        value = parse_br_date(filters.get(key))
        if value:
            filters[key] = value.isoformat()
    return filters


def _has_invalid_date_filter(request):
    return any(
        request.GET.get(key, '').strip() and not parse_br_date(request.GET.get(key))
        for key in ('date_from', 'date_to')
    )


# ---------------------------------------------------------------------------
# Global financial views (R5.01-R5.09)
# ---------------------------------------------------------------------------

class FinancialDashboardView(BillingAccessMixin, TemplateView):
    """Financial module dashboard with KPIs (R5.01, R5.02)."""

    template_name = 'billing/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(financial_kpis())
        return context


class GlobalReceivableListView(BillingAccessMixin, ListView):
    """Paginated list of all receivables with filters (R5.04)."""

    template_name = 'billing/receivable_list_global.html'
    context_object_name = 'receivables'
    paginate_by = 30

    def get_queryset(self):
        qs = Receivable.objects.select_related(
            'rental', 'rental__customer'
        ).order_by('due_date', 'rental__number')

        status = self.request.GET.get('status', 'open')
        if status == 'open':
            qs = qs.filter(balance__gt=0)
        elif status == 'paid':
            qs = qs.filter(balance__lte=0)

        if self.request.GET.get('overdue'):
            qs = qs.filter(due_date__lt=date_cls.today(), balance__gt=0)

        date_from = parse_br_date(self.request.GET.get('date_from'))
        date_to = parse_br_date(self.request.GET.get('date_to'))
        if _has_invalid_date_filter(self.request):
            messages.error(self.request, 'Informe as datas no formato dd/mm/aaaa.')
            return qs.none()
        if date_from:
            qs = qs.filter(due_date__gte=date_from)
        if date_to:
            qs = qs.filter(due_date__lte=date_to)

        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(rental__customer__name_search__icontains=_normalize_name(q))

        rental_number = self.request.GET.get('locacao', '').strip()
        if rental_number.isdigit():
            qs = qs.filter(rental__number=rental_number)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = Company.load()
        rows = [
            {'obj': rec, **interest_breakdown(rec, company=company)}
            for rec in context['receivables']
        ]
        context['rows'] = rows
        context['filters'] = _filters_for_display(self.request)
        context['today'] = date_cls.today()
        return context

class CustomerReceivableView(BillingAccessMixin, TemplateView):
    """Receivables filtered by customer, plus totals (R5.05)."""

    template_name = 'billing/customer_receivables.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        q = self.request.GET.get('q', '').strip()
        customer_pk = self.kwargs.get('pk')
        customer = None
        customer_results = []

        if customer_pk:
            customer = get_object_or_404(Customer, pk=customer_pk)
        elif q:
            customer_results = Customer.objects.filter(name_search__icontains=_normalize_name(q)).order_by('name')[:20]

        receivable_rows = []
        total_balance = Decimal('0')
        total_with_int = Decimal('0')

        if customer:
            company = Company.load()
            recs = (
                Receivable.objects.filter(rental__customer=customer, balance__gt=0)
                .select_related('rental')
                .order_by('due_date')
            )
            for rec in recs:
                breakdown = interest_breakdown(rec, company=company)
                receivable_rows.append({
                    'obj': rec,
                    **breakdown,
                })
                total_balance += rec.balance
                total_with_int += breakdown['total_with_interest']

        context.update({
            'customer': customer,
            'customer_results': customer_results,
            'receivable_rows': receivable_rows,
            'total_balance': total_balance,
            'total_with_interest': total_with_int,
            'q': q,
            'multi_pay_form': MultiPayForm(initial={'payment_date': date_cls.today()}),
        })
        return context


class ReceivablePayView(BillingAccessMixin, ActionRequiredMixin, FormView):
    """Pay a single receivable — creates Payment + FinancialMovement (R5.06, R5.08)."""

    action_key = 'billing.receive'

    form_class = ReceivablePayForm
    template_name = 'billing/receivable_pay.html'

    def dispatch(self, request, *args, **kwargs):
        self.receivable = get_object_or_404(
            Receivable.objects.select_related('rental__customer'), pk=kwargs['pk']
        )
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        company = Company.load()
        breakdown = interest_breakdown(self.receivable, company=company)
        return {
            'amount': breakdown['total_with_interest'],
            'payment_date': date_cls.today(),
            'interest_amount': breakdown['interest'],
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = Company.load()
        breakdown = interest_breakdown(self.receivable, company=company)
        context.update({
            'receivable': self.receivable,
            **breakdown,
        })
        return context

    def form_valid(self, form):
        amount = form.cleaned_data['amount']
        expected_total = total_with_interest(self.receivable, company=Company.load())

        if amount > expected_total and not form.cleaned_data.get('confirm_overpayment'):
            form.add_error(
                'confirm_overpayment',
                f'Valor acima do total com juros (R$ {expected_total:.2f}). Marque para confirmar.'
            )
            return self.form_invalid(form)

        register_payment(
            receivable=self.receivable,
            amount=amount,
            payment_date=form.cleaned_data['payment_date'],
            method=form.cleaned_data['method'],
            interest_amount=form.cleaned_data.get('interest_amount'),
            discount_amount=form.cleaned_data.get('discount_amount'),
            notes=form.cleaned_data.get('notes', ''),
            user=self.request.user,
        )
        messages.success(self.request, 'Recebimento registrado com sucesso.')

        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return redirect(next_url)
        return redirect('billing:customer_receivables', pk=self.receivable.rental.customer_id)


class MultiPayView(BillingAccessMixin, ActionRequiredMixin, FormView):
    """Distribute a payment amount across selected receivables for a customer (R5.07)."""

    action_key = 'billing.receive'

    template_name = 'billing/multi_pay.html'
    form_class = MultiPayForm

    def dispatch(self, request, *args, **kwargs):
        self.customer = get_object_or_404(Customer, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {'payment_date': date_cls.today()}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        recs = (
            Receivable.objects.filter(rental__customer=self.customer, balance__gt=0)
            .select_related('rental')
            .order_by('due_date')
        )
        rows = []
        total_balance = Decimal('0')
        company = Company.load()
        for rec in recs:
            breakdown = interest_breakdown(rec, company=company)
            rows.append({
                'obj': rec,
                **breakdown,
            })
            total_balance += rec.balance
        context.update({
            'customer': self.customer,
            'receivable_rows': rows,
            'total_balance': total_balance,
        })
        return context

    def form_valid(self, form):
        receivable_ids = self.request.POST.getlist('receivable_ids')
        if not receivable_ids:
            messages.error(self.request, 'Selecione pelo menos um título.')
            return self.form_invalid(form)

        with transaction.atomic():
            # Lock and recalculate the selected balances inside the transaction.
            # A second cashier may have paid one of these titles after the page
            # was opened, so a pre-lock total must never drive the allocation.
            selected = list(
                Receivable.objects.select_for_update()
                .filter(
                    pk__in=receivable_ids,
                    rental__customer=self.customer,
                    balance__gt=0,
                )
                .select_related('rental')
                .order_by('due_date')
            )
            selected_total = sum((rec.balance for rec in selected), Decimal('0'))
            if not selected_total:
                form.add_error('total_amount', 'Nenhum título em aberto foi selecionado.')
                return self.form_invalid(form)

            if form.cleaned_data['total_amount'] > selected_total:
                form.add_error(
                    'total_amount',
                    f'O valor informado é maior que o saldo dos títulos selecionados (R$ {selected_total:.2f}).',
                )
                return self.form_invalid(form)

            remaining = form.cleaned_data['total_amount']
            paid_count = 0
            for rec in selected:
                if remaining <= 0:
                    break
                pay_amount = min(remaining, rec.balance)
                register_payment(
                    receivable=rec,
                    amount=pay_amount,
                    payment_date=form.cleaned_data['payment_date'],
                    method=form.cleaned_data['method'],
                    notes=form.cleaned_data.get('notes', ''),
                    user=self.request.user,
                )
                remaining -= pay_amount
                paid_count += 1

        messages.success(self.request, f'{paid_count} título(s) recebido(s) com sucesso.')
        return redirect('billing:customer_receivables', pk=self.customer.pk)


class PaymentReversalView(BillingAccessMixin, ActionRequiredMixin, FormView):
    """Reverse a payment, creating an outflow movement (R5.09)."""

    action_key = 'billing.reverse'

    template_name = 'billing/payment_reversal.html'
    form_class = ReversalForm

    def dispatch(self, request, *args, **kwargs):
        self.payment = get_object_or_404(Payment, pk=kwargs['pk'])
        if self.payment.is_reversal or self.payment.reversed_by_id is not None:
            messages.error(request, 'Este recebimento já foi estornado.')
            if self.payment.customer_id:
                return redirect('billing:customer_receivables', pk=self.payment.customer_id)
            return redirect('billing:receivables')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['payment'] = self.payment
        return context

    def form_valid(self, form):
        reverse_payment(
            self.payment,
            reason=form.cleaned_data['reason'],
            user=self.request.user,
        )
        messages.success(self.request, 'Estorno registrado com sucesso.')
        if self.payment.customer_id:
            return redirect('billing:customer_receivables', pk=self.payment.customer_id)
        return redirect('billing:receivables')


# ---------------------------------------------------------------------------
# R6 — Cash movements, reports and reconciliation
# ---------------------------------------------------------------------------

class CashMovementListView(BillingAccessMixin, ListView):
    """Paginated cash movement log with filters and totals (R6.01)."""

    template_name = 'billing/cash_movement_list.html'
    context_object_name = 'movements'
    paginate_by = 50

    def get_queryset(self):
        qs = FinancialMovement.objects.select_related(
            'account', 'customer', 'receivable', 'rental'
        ).order_by('-date', '-created_at')

        date_from = parse_br_date(self.request.GET.get('date_from'))
        date_to = parse_br_date(self.request.GET.get('date_to'))
        if _has_invalid_date_filter(self.request):
            messages.error(self.request, 'Informe as datas no formato dd/mm/aaaa.')
            return qs.none()
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        direction = self.request.GET.get('direction')
        if direction in ('inflow', 'outflow'):
            qs = qs.filter(direction=direction)

        account_id = self.request.GET.get('account')
        if account_id and account_id.isdigit():
            qs = qs.filter(account_id=account_id)

        source = self.request.GET.get('source')
        if source:
            qs = qs.filter(source=source)

        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(customer__name_search__icontains=_normalize_name(q))

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Totals across full queryset (not just current page)
        qs = self.get_queryset()
        inflow = qs.filter(direction=FinancialMovement.Direction.INFLOW).aggregate(
            v=Sum('amount')
        )['v'] or Decimal('0')
        outflow = qs.filter(direction=FinancialMovement.Direction.OUTFLOW).aggregate(
            v=Sum('amount')
        )['v'] or Decimal('0')
        context.update({
            'total_inflow': inflow,
            'total_outflow': outflow,
            'balance': inflow - outflow,
            'accounts': CashAccount.objects.filter(active=True),
            'sources': FinancialMovement.Source.choices,
            'filters': _filters_for_display(self.request),
        })
        return context


class ManualCashMovementView(BillingAccessMixin, ActionRequiredMixin, FormView):
    """Record a manual cash movement (R6.02)."""

    action_key = 'billing.cash'

    template_name = 'billing/manual_movement_form.html'
    form_class = ManualMovementForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['accounts'] = CashAccount.objects.filter(active=True)
        return context

    def form_valid(self, form):
        movement = FinancialMovement.objects.create(
            date=form.cleaned_data['date'],
            account=form.cleaned_data['account'],
            direction=form.cleaned_data['direction'],
            amount=form.cleaned_data['amount'],
            description=form.cleaned_data['description'],
            source=FinancialMovement.Source.MANUAL,
            customer=form.cleaned_data.get('customer'),
            created_by=self.request.user,
        )
        from core.models import AuditLog
        AuditLog.objects.create(
            user=self.request.user,
            action='cash_manual',
            model_name='FinancialMovement',
            object_id=str(movement.pk),
            object_repr=str(movement),
            reason=f'Lançamento manual: {movement.description or ""}',
        )
        messages.success(self.request, 'Movimento registrado com sucesso.')
        return redirect('billing:cash_movements')


class PaymentReportView(BillingAccessMixin, ListView):
    """Report of received payments by period (R6.03)."""

    template_name = 'billing/payment_report.html'
    context_object_name = 'payments'
    paginate_by = 100

    def get_queryset(self):
        qs = (
            Payment.objects.filter(is_reversal=False)
            .select_related('receivable', 'receivable__rental', 'customer', 'user')
            .order_by('-payment_date', '-created_at')
        )

        date_from = parse_br_date(self.request.GET.get('date_from'))
        date_to = parse_br_date(self.request.GET.get('date_to'))
        if _has_invalid_date_filter(self.request):
            messages.error(self.request, 'Informe as datas no formato dd/mm/aaaa.')
            return qs.none()
        if date_from:
            qs = qs.filter(payment_date__gte=date_from)
        if date_to:
            qs = qs.filter(payment_date__lte=date_to)

        method = self.request.GET.get('method')
        if method:
            qs = qs.filter(method=method)

        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(customer__name_search__icontains=_normalize_name(q))

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        total = qs.aggregate(v=Sum('amount'))['v'] or Decimal('0')
        context.update({
            'total_received': total,
            'methods': Payment.Method.choices,
            'filters': _filters_for_display(self.request),
            'today': date_cls.today(),
        })
        return context


class CashMovementReportView(BillingAccessMixin, TemplateView):
    """Cash movement summary report by period (R6.04)."""

    template_name = 'billing/cash_movement_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = date_cls.today()
        invalid_dates = _has_invalid_date_filter(self.request)
        date_from = parse_br_date(self.request.GET.get('date_from')) or today.replace(day=1)
        date_to = parse_br_date(self.request.GET.get('date_to')) or today

        qs = FinancialMovement.objects.filter(date__gte=date_from, date__lte=date_to)
        if invalid_dates:
            messages.error(self.request, 'Informe as datas no formato dd/mm/aaaa.')
            qs = qs.none()

        account_id = self.request.GET.get('account')
        if account_id and account_id.isdigit():
            qs = qs.filter(account_id=account_id)

        # Totals by direction
        inflow = qs.filter(direction=FinancialMovement.Direction.INFLOW).aggregate(
            v=Sum('amount')
        )['v'] or Decimal('0')
        outflow = qs.filter(direction=FinancialMovement.Direction.OUTFLOW).aggregate(
            v=Sum('amount')
        )['v'] or Decimal('0')

        # Breakdown by source
        source_breakdown = []
        for source_code, source_label in FinancialMovement.Source.choices:
            src_qs = qs.filter(source=source_code)
            src_in = src_qs.filter(direction=FinancialMovement.Direction.INFLOW).aggregate(
                v=Sum('amount')
            )['v'] or Decimal('0')
            src_out = src_qs.filter(direction=FinancialMovement.Direction.OUTFLOW).aggregate(
                v=Sum('amount')
            )['v'] or Decimal('0')
            if src_in or src_out:
                source_breakdown.append({
                    'source': source_label,
                    'inflow': src_in,
                    'outflow': src_out,
                    'net': src_in - src_out,
                })

        movements = qs.select_related('account', 'customer').order_by('-date', '-created_at')[:200]

        context.update({
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'total_inflow': inflow,
            'total_outflow': outflow,
            'balance': inflow - outflow,
            'source_breakdown': source_breakdown,
            'movements': movements,
            'accounts': CashAccount.objects.filter(active=True),
            'filters': self.request.GET,
            'today': today,
        })
        return context


class ReconciliationView(BillingAccessMixin, TemplateView):
    """Financial reconciliation — compares receivables, payments and movements (R6.05)."""

    template_name = 'billing/reconciliation.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['recon'] = reconcile_financial()
        context['today'] = date_cls.today()
        return context


class ReconciliationExportView(BillingAccessMixin, View):
    """Export reconciliation divergences as CSV (R6.06)."""

    def get(self, request, *args, **kwargs):
        recon = reconcile_financial()
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="reconciliacao.csv"'
        response.write('﻿')  # UTF-8 BOM for Excel compatibility

        writer = csv.writer(response, delimiter=';')
        writer.writerow(['Tipo de divergência', 'ID', 'Locação', 'Vencimento',
                         'Valor título', 'Recebido armazenado', 'Soma recebimentos', 'Diferença'])

        for item in recon['inconsistent_balances']:
            writer.writerow([
                'Saldo inconsistente',
                item['id'],
                item['rental_number'] or '',
                item['due_date'],
                str(item['amount']).replace('.', ','),
                str(item['paid_amount_stored']).replace('.', ','),
                str(item['payment_sum']).replace('.', ','),
                str(item['diff']).replace('.', ','),
            ])

        return response


# ---------------------------------------------------------------------------
# Rental-scoped views (kept for rental detail page integration)
# ---------------------------------------------------------------------------

class ReceivableListView(BillingAccessMixin, ListView):
    """List receivables of a rental with total including interest (RF-19, RF-20)."""

    template_name = 'billing/receivable_list.html'
    context_object_name = 'receivables'

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Receivable.objects.filter(rental=self.rental).select_related('rental__customer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = Company.load()
        rows = [
            {'obj': rec, **interest_breakdown(rec, company=company)}
            for rec in context['receivables']
        ]
        context['rows'] = rows
        context['rental'] = self.rental
        context['generate_form'] = GenerateReceivablesForm()
        return context


class GenerateReceivablesView(BillingAccessMixin, FormView):
    """Generate installments for a rental (RF-19 / 8.1.3)."""

    form_class = GenerateReceivablesForm
    template_name = 'billing/receivable_list.html'

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        has_payments = self.rental.receivables.filter(
            Q(paid_amount__gt=0) | Q(payments__isnull=False)
        ).exists()
        if has_payments:
            messages.error(
                self.request,
                'Não é possível re-gerar as parcelas pois esta locação já possui recebimentos registrados.'
            )
            return redirect('billing:list', rental_pk=self.rental.pk)

        with transaction.atomic():
            self.rental.receivables.all().delete()
            generate_for_rental(
                self.rental,
                installments=form.cleaned_data['installments'],
                first_due_date=form.cleaned_data.get('first_due_date'),
            )
        messages.success(self.request, 'Parcelas geradas com sucesso.')
        return redirect('billing:list', rental_pk=self.rental.pk)

    def form_invalid(self, form):
        messages.error(self.request, 'Não foi possível gerar as parcelas.')
        return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = Company.load()
        context.update({
            'rental': self.rental,
            'rows': [
                {'obj': receivable, **interest_breakdown(receivable, company=company)}
                for receivable in self.rental.receivables.select_related('rental__customer')
            ],
            'generate_form': context['form'],
        })
        return context


class PaymentView(BillingAccessMixin, ActionRequiredMixin, FormView):
    """Legacy payment view kept for backward compatibility (RF-21)."""

    form_class = PaymentForm
    template_name = 'billing/payment_form.html'
    action_key = 'billing.receive'

    def dispatch(self, request, *args, **kwargs):
        self.receivable = get_object_or_404(Receivable, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['receivable'] = self.receivable
        context['total_with_interest'] = total_with_interest(self.receivable)
        return context

    def form_valid(self, form):
        register_payment(
            receivable=self.receivable,
            amount=form.cleaned_data['value'],
            payment_date=form.cleaned_data['payment_date'],
            method=Payment.Method.CASH,
            user=self.request.user,
        )
        messages.success(self.request, 'Recebimento registrado com sucesso.')
        return redirect('billing:list', rental_pk=self.receivable.rental_id)
