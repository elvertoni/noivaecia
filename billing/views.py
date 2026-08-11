import csv
import uuid
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import F, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
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
    PaymentPlanError,
    ReceiptIdempotencyConflict,
    allocated_cash,
    financial_kpis,
    interest_breakdown,
    plan_carryover_allocations,
    plan_selected_allocations,
    reconcile_financial,
    register_receipt,
    reprocess_future_installments,
    revert_write_off_receivable,
    reverse_payment,
    reverse_receipt,
    total_with_interest,
)


class BillingAccessMixin(ModuleAccessMixin):
    module_key = 'billing'


# Namespace for deriving receipt idempotency keys. The browser-supplied
# submission token is never used as a key directly: hashing it together with a
# server-controlled scope keeps a crafted token from colliding with an existing
# receipt and silently swallowing real money.
RECEIPT_KEY_NAMESPACE = uuid.UUID('4d0f7d4b-6f0e-5b1a-9c2d-0b7a1f3e5c81')

NO_ACTIVE_CASH_ACCOUNT_MESSAGE = (
    'Não é possível registrar recebimento sem uma conta caixa ativa. '
    'Configure uma conta em Financeiro > Contas.'
)

DUPLICATE_SUBMISSION_MESSAGE = (
    'Este recebimento já foi enviado com outros valores. Recarregue a tela e '
    'confira o que foi registrado antes de tentar de novo.'
)


def _receipt_key(scope, token):
    """Derive a stable receipt idempotency key from one page submission."""
    return uuid.uuid5(RECEIPT_KEY_NAMESPACE, f'{scope}:{token}')


def _active_cash_account():
    """First active cash account by id — the criterion the services use.

    Locked with the rest of the financial write so the canonical lock order
    (Rental -> Receivable -> CashAccount) is preserved.
    """
    account = (
        CashAccount.objects.select_for_update()
        .filter(active=True)
        .order_by('id')
        .first()
    )
    if account is None:
        raise ValueError(NO_ACTIVE_CASH_ACCOUNT_MESSAGE)
    return account


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
            qs = qs.filter(due_date__lt=timezone.localdate(), balance__gt=0)

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
        context['today'] = timezone.localdate()
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
            'multi_pay_form': MultiPayForm(
                initial={'payment_date': timezone.localdate()},
            ),
        })
        return context


class ReceivablePayView(BillingAccessMixin, ActionRequiredMixin, FormView):
    """Receive one title as a single-allocation receipt (R5.06, R5.08)."""

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
            'payment_date': timezone.localdate(),
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

        if amount > expected_total:
            form.add_error(
                'amount',
                f'O valor não pode superar o total com juros (R$ {expected_total:.2f}).'
            )
            return self.form_invalid(form)

        try:
            with transaction.atomic():
                account = _active_cash_account()
                register_receipt(
                    idempotency_key=_receipt_key(
                        f'billing:pay_receivable:{self.receivable.pk}',
                        form.cleaned_data['submission_token'],
                    ),
                    payload={
                        'rental_id': self.receivable.rental_id,
                        'cash_account_id': account.pk,
                        'received_on': form.cleaned_data['payment_date'],
                        'amount': amount,
                        'method': form.cleaned_data['method'],
                        'notes': form.cleaned_data.get('notes', ''),
                        'allocations': [{
                            'receivable_id': self.receivable.pk,
                            'cash_amount': amount,
                            'interest_amount': (
                                form.cleaned_data.get('interest_amount') or 0
                            ),
                            'discount_amount': (
                                form.cleaned_data.get('discount_amount') or 0
                            ),
                        }],
                    },
                    user=self.request.user,
                )
        except ReceiptIdempotencyConflict:
            messages.error(self.request, DUPLICATE_SUBMISSION_MESSAGE)
            return self.form_invalid(form)
        except ValueError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)
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
        return {'payment_date': timezone.localdate()}

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

        try:
            with transaction.atomic():
                rental_ids = list(
                    Receivable.objects.filter(
                        pk__in=receivable_ids,
                        rental__customer=self.customer,
                    )
                    .values_list('rental_id', flat=True)
                    .distinct()
                )
                # All financial writers use the same lock hierarchy to avoid
                # PostgreSQL deadlocks: Rental -> Receivable -> CashAccount.
                list(
                    Rental.objects.select_for_update()
                    .filter(pk__in=rental_ids)
                    .order_by('pk')
                )
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
                    .order_by('due_date', 'pk')
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

                allocations = plan_selected_allocations(
                    receivables=selected,
                    amount=form.cleaned_data['total_amount'],
                )
                if not allocations:
                    form.add_error('total_amount', 'Nenhum título em aberto foi selecionado.')
                    return self.form_invalid(form)

                # A receipt belongs to exactly one rental, so a customer paying
                # across rentals gets one receipt per rental. They are written
                # inside this single transaction, so the whole cash act still
                # succeeds or fails together, and each receipt stays reversible
                # as a coherent unit.
                receivable_by_id = {rec.pk: rec for rec in selected}
                grouped = {}
                for allocation in allocations:
                    rental_id = receivable_by_id[allocation['receivable_id']].rental_id
                    grouped.setdefault(rental_id, []).append(allocation)

                account = _active_cash_account()
                paid_count = len(allocations)
                receipt_count = 0
                for rental_id, rental_allocations in grouped.items():
                    register_receipt(
                        idempotency_key=_receipt_key(
                            f'billing:multi_pay:{self.customer.pk}:{rental_id}',
                            form.cleaned_data['submission_token'],
                        ),
                        payload={
                            'rental_id': rental_id,
                            'cash_account_id': account.pk,
                            'received_on': form.cleaned_data['payment_date'],
                            'amount': allocated_cash(rental_allocations),
                            'method': form.cleaned_data['method'],
                            'notes': form.cleaned_data.get('notes', ''),
                            'allocations': rental_allocations,
                        },
                        user=self.request.user,
                    )
                    receipt_count += 1
        except ReceiptIdempotencyConflict:
            messages.error(self.request, DUPLICATE_SUBMISSION_MESSAGE)
            return self.form_invalid(form)
        except ValueError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        if receipt_count > 1:
            messages.success(
                self.request,
                f'{paid_count} título(s) recebido(s) com sucesso em '
                f'{receipt_count} recibos, um por locação.',
            )
        else:
            messages.success(self.request, f'{paid_count} título(s) recebido(s) com sucesso.')
        return redirect('billing:customer_receivables', pk=self.customer.pk)


class PaymentReversalView(BillingAccessMixin, ActionRequiredMixin, FormView):
    """Undo one cash act, creating a single outflow movement (R5.09).

    A payment that belongs to a receipt is only a slice of the money that
    crossed the counter, so reversing it alone would leave the rest of the
    receipt standing and mint a second cash movement over an inflow that the
    ``Receipt`` model already owns. Those are routed to ``reverse_receipt``,
    which undoes the whole act atomically. Payments predating the receipt model
    keep the legacy per-payment path.
    """

    action_key = 'billing.reverse'

    template_name = 'billing/payment_reversal.html'
    form_class = ReversalForm

    def dispatch(self, request, *args, **kwargs):
        self.payment = get_object_or_404(
            Payment.objects.select_related(
                'receipt_allocation__receipt',
                'receivable__rental',
            ),
            pk=kwargs['pk'],
        )
        allocation = getattr(self.payment, 'receipt_allocation', None)
        self.receipt = allocation.receipt if allocation is not None else None
        already_reversed = (
            self.payment.is_reversal
            or self.payment.reversed_by_id is not None
            or (self.receipt is not None and hasattr(self.receipt, 'reversal'))
        )
        if already_reversed:
            messages.error(request, 'Este recebimento já foi estornado.')
            if self.payment.customer_id:
                return redirect('billing:customer_receivables', pk=self.payment.customer_id)
            return redirect('billing:receivables')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['payment'] = self.payment
        context['receipt'] = self.receipt
        if self.receipt is not None:
            # The operator must see the whole act they are undoing, not just
            # the slice they clicked on.
            context['receipt_allocations'] = list(
                self.receipt.allocations
                .select_related('receivable__rental')
                .order_by('receivable__due_date', 'receivable_id')
            )
        return context

    def form_valid(self, form):
        reason = form.cleaned_data['reason']
        try:
            if self.receipt is not None:
                reverse_receipt(
                    self.receipt,
                    idempotency_key=_receipt_key(
                        f'billing:reverse_receipt:{self.receipt.pk}',
                        form.cleaned_data['submission_token'],
                    ),
                    payload={
                        'received_on': timezone.localdate(),
                        'reason': reason,
                    },
                    user=self.request.user,
                )
            else:
                reverse_payment(
                    self.payment,
                    reason=reason,
                    user=self.request.user,
                )
        except ReceiptIdempotencyConflict:
            messages.error(self.request, DUPLICATE_SUBMISSION_MESSAGE)
            return redirect('billing:receivables')
        except ValueError as exc:
            messages.error(self.request, str(exc))
            return redirect('billing:receivables')
        messages.success(self.request, 'Estorno registrado com sucesso.')
        if self.payment.customer_id:
            return redirect('billing:customer_receivables', pk=self.payment.customer_id)
        return redirect('billing:receivables')


class ReceivableReopenView(BillingAccessMixin, ActionRequiredMixin, View):
    """Reopen a written-off receivable so payments can be registered again."""

    action_key = 'billing.reopen'
    action_methods = ('POST',)

    def post(self, request, *args, **kwargs):
        receivable = get_object_or_404(Receivable, pk=kwargs['pk'])
        try:
            revert_write_off_receivable(receivable, user=request.user)
            messages.success(
                request,
                f'Baixa do recebimento (vencimento {receivable.due_date.strftime("%d/%m/%Y")}) revertida com sucesso.'
            )
        except ValueError as exc:
            messages.error(request, str(exc))

        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect('billing:list', rental_pk=receivable.rental_id)


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
        qs = self.object_list
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
        from core.models import AuditLog
        with transaction.atomic():
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
    """Report of received cash acts by period (R6.03).

    One receipt can settle several titles, and each of those settlements is a
    ``Payment``. Listing payments one per row would inflate the number of cash
    acts, so rows of the same receipt are folded back into a single line. The
    ordering keys on the receipt so its payments stay adjacent; the period total
    still sums every payment, which keeps the money identical either way.
    """

    template_name = 'billing/payment_report.html'
    context_object_name = 'payments'
    paginate_by = 100

    def get_queryset(self):
        qs = (
            Payment.objects.filter(is_reversal=False, reversed_by__isnull=True)
            .select_related('receivable', 'receivable__rental', 'customer', 'user')
            .annotate(
                receipt_group_id=F('receipt_allocation__receipt_id'),
                receipt_group_order=Coalesce(
                    'receipt_allocation__receipt__created_at',
                    'created_at',
                ),
            )
            .order_by(
                '-payment_date',
                '-receipt_group_order',
                'receipt_group_id',
                'pk',
            )
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

    @staticmethod
    def _fold_into_cash_acts(payments):
        """Collapse the payments of one receipt into a single report row."""
        rows = []
        for payment in payments:
            group_id = payment.receipt_group_id
            if group_id and rows and rows[-1]['receipt_id'] == group_id:
                row = rows[-1]
                row['amount'] += payment.amount
                row['payments'].append(payment)
                continue
            rental = payment.receivable.rental if payment.receivable_id else None
            rows.append({
                'receipt_id': group_id,
                'payment': payment,
                'payments': [payment],
                'payment_date': payment.payment_date,
                'customer': payment.customer or (rental.customer if rental else None),
                'rental_number': rental.number if rental else None,
                'method_display': payment.get_method_display(),
                'notes': payment.notes,
                'amount': payment.amount,
            })
        for row in rows:
            row['title_count'] = len(row['payments'])
        return rows

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.object_list
        total = qs.aggregate(v=Sum('amount'))['v'] or Decimal('0')
        context.update({
            'cash_acts': self._fold_into_cash_acts(context['payments']),
            'total_received': total,
            'methods': Payment.Method.choices,
            'filters': _filters_for_display(self.request),
            'today': timezone.localdate(),
        })
        return context


class CashMovementReportView(BillingAccessMixin, TemplateView):
    """Cash movement summary report by period (R6.04)."""

    template_name = 'billing/cash_movement_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
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
        grouped = {
            (row['source'], row['direction']): row['total']
            for row in qs.values('source', 'direction').annotate(total=Sum('amount'))
        }
        source_breakdown = []
        for source_code, source_label in FinancialMovement.Source.choices:
            src_in = grouped.get(
                (source_code, FinancialMovement.Direction.INFLOW),
                Decimal('0'),
            )
            src_out = grouped.get(
                (source_code, FinancialMovement.Direction.OUTFLOW),
                Decimal('0'),
            )
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
        context['today'] = timezone.localdate()
        return context


class ReconciliationExportView(
    BillingAccessMixin,
    ActionRequiredMixin,
    View,
):
    """Export reconciliation divergences as CSV (R6.06)."""

    action_key = 'reports.export'
    action_methods = ('GET', 'HEAD')

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

        for payment_id in recon['payments_with_movement_issue_ids']:
            writer.writerow(['Recebimento sem movimento de caixa válido único', payment_id])
        for payment_id in recon['reversals_with_movement_issue_ids']:
            writer.writerow(['Estorno sem movimento de caixa válido único', payment_id])

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


class GenerateReceivablesView(
    BillingAccessMixin,
    ActionRequiredMixin,
    FormView,
):
    """Generate installments for a rental (RF-19 / 8.1.3)."""

    action_key = 'billing.receive'
    form_class = GenerateReceivablesForm
    template_name = 'billing/receivable_list.html'

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs['rental_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            result = reprocess_future_installments(
                self.rental,
                installments=form.cleaned_data['installments'],
                first_due_date=form.cleaned_data.get('first_due_date'),
                user=self.request.user,
            )
        except PaymentPlanError as exc:
            messages.error(
                self.request,
                str(exc),
            )
            return redirect('billing:list', rental_pk=self.rental.pk)

        if result['protected']:
            messages.success(
                self.request,
                'Parcelas futuras atualizadas. Os recebimentos já registrados foram preservados.',
            )
        else:
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
    """Receive on one installment, spilling the surplus into the rest (RF-21).

    The whole act is a single ``Receipt``: one cash movement, one allocation per
    installment it settles. That is what makes it reversible in one step later.
    """

    form_class = PaymentForm
    template_name = 'billing/payment_form.html'
    action_key = 'billing.receive'

    def dispatch(self, request, *args, **kwargs):
        self.receivable = get_object_or_404(Receivable, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        title_total = total_with_interest(self.receivable)
        others_balance = (
            Receivable.objects.filter(
                rental_id=self.receivable.rental_id,
                balance__gt=0,
                written_off_at__isnull=True,
            )
            .exclude(pk=self.receivable.pk)
            .aggregate(total=Sum('balance'))['total'] or Decimal('0')
        )
        context['receivable'] = self.receivable
        context['total_with_interest'] = title_total
        context['rental_open_total'] = title_total + others_balance
        context['has_future_installments'] = others_balance > 0
        return context

    def form_valid(self, form):
        try:
            with transaction.atomic():
                # Canonical lock order across every financial writer:
                # Rental -> Receivable -> CashAccount.
                Rental.objects.select_for_update().only('pk').get(
                    pk=self.receivable.rental_id,
                )
                schedule = list(
                    Receivable.objects.select_for_update(of=('self',))
                    .filter(rental_id=self.receivable.rental_id)
                    .order_by('due_date', 'pk')
                )
                target = next(
                    (item for item in schedule if item.pk == self.receivable.pk),
                    None,
                )
                if target is None:
                    raise ValueError(
                        'O título informado não pertence mais a esta locação.'
                    )
                allocations = plan_carryover_allocations(
                    target=target,
                    schedule=schedule,
                    amount=form.cleaned_data['value'],
                    target_interest=total_with_interest(target) - target.balance,
                )
                account = _active_cash_account()
                receipt = register_receipt(
                    idempotency_key=_receipt_key(
                        f'billing:pay:{self.receivable.pk}',
                        form.cleaned_data['submission_token'],
                    ),
                    payload={
                        'rental_id': self.receivable.rental_id,
                        'cash_account_id': account.pk,
                        'received_on': form.cleaned_data['payment_date'],
                        'amount': allocated_cash(allocations),
                        'method': Payment.Method.CASH,
                        'notes': '',
                        'allocations': allocations,
                    },
                    user=self.request.user,
                )
        except PaymentPlanError as exc:
            form.add_error(exc.field or 'value', str(exc))
            return self.form_invalid(form)
        except ReceiptIdempotencyConflict:
            messages.error(self.request, DUPLICATE_SUBMISSION_MESSAGE)
            return self.form_invalid(form)
        except ValueError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)
        installment_count = receipt.allocations.count()
        if installment_count > 1:
            messages.success(
                self.request,
                'Recebimento registrado com sucesso e distribuído entre '
                f'{installment_count} parcelas.',
            )
        else:
            messages.success(self.request, 'Recebimento registrado com sucesso.')
        return redirect('billing:list', rental_pk=self.receivable.rental_id)
