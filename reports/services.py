"""Isolated query services for each report type (R11.01)."""
from datetime import date as date_cls
from decimal import Decimal

from django.db.models import Q, Sum

from billing.models import Receivable
from rentals.models import Rental


def _rental_base(status_filter=None):
    qs = (
        Rental.objects.select_related('customer')
        .prefetch_related('items__product__category', 'pickup', 'return_record')
    )
    if status_filter:
        qs = qs.filter(status=status_filter)
    return qs


def _apply_rental_filters(qs, *, date_from='', date_to='', customer='', prefix='', code='', date_field='pickup_date'):
    if date_from:
        qs = qs.filter(**{f'{date_field}__gte': date_from})
    if date_to:
        qs = qs.filter(**{f'{date_field}__lte': date_to})
    if customer:
        qs = qs.filter(customer__name__icontains=customer)
    if prefix:
        qs = qs.filter(items__product__category__prefix__iexact=prefix)
    if code:
        try:
            qs = qs.filter(items__product__code=int(code))
        except ValueError:
            qs = qs.none()
    return qs.distinct().order_by('-number')


def report_a_retirar(date_from='', date_to='', customer='', prefix='', code=''):
    """Rentals pending pickup (R11.02 — equiv. locados.rpt não retirados)."""
    qs = _rental_base(Rental.Status.PENDING)
    return _apply_rental_filters(qs, date_from=date_from, date_to=date_to,
                                  customer=customer, prefix=prefix, code=code)


def report_retirados(date_from='', date_to='', customer='', prefix='', code=''):
    """Rentals picked up (R11.03 — equiv. locados12.rpt retirados)."""
    qs = _rental_base(Rental.Status.PICKED_UP)
    return _apply_rental_filters(qs, date_from=date_from, date_to=date_to,
                                  customer=customer, prefix=prefix, code=code,
                                  date_field='pickup__pickup_date')


def report_devolvidos(date_from='', date_to='', customer='', prefix='', code=''):
    """Rentals returned (R11.04)."""
    qs = _rental_base(Rental.Status.RETURNED)
    return _apply_rental_filters(qs, date_from=date_from, date_to=date_to,
                                  customer=customer, prefix=prefix, code=code,
                                  date_field='return_record__return_date')


def report_atrasados(customer='', prefix='', code=''):
    """Picked-up rentals past return date (R11.05)."""
    today = date_cls.today()
    qs = _rental_base(Rental.Status.PICKED_UP).filter(return_date__lt=today)
    qs = _apply_rental_filters(qs, customer=customer, prefix=prefix, code=code)
    rows = []
    for r in qs:
        rows.append({'rental': r, 'days_late': (today - r.return_date).days})
    return rows


def report_locacoes(date_from='', date_to='', customer='', status=''):
    """All rentals (realized) — equiv. vendas.rpt (R11.06)."""
    qs = _rental_base().exclude(status=Rental.Status.CANCELLED)
    if status:
        qs = qs.filter(status=status)
    return _apply_rental_filters(qs, date_from=date_from, date_to=date_to, customer=customer)


def report_contas_vencimento(date_from='', date_to='', customer='', overdue_only=False):
    """Open receivables by due date — equiv. receber.rpt (R11.07)."""
    today = date_cls.today()
    qs = (
        Receivable.objects.filter(balance__gt=0)
        .select_related('rental__customer')
        .order_by('due_date', 'rental__number')
    )
    if date_from:
        qs = qs.filter(due_date__gte=date_from)
    if date_to:
        qs = qs.filter(due_date__lte=date_to)
    if customer:
        qs = qs.filter(rental__customer__name__icontains=customer)
    if overdue_only:
        qs = qs.filter(due_date__lt=today)
    totals = qs.aggregate(t_amount=Sum('amount'), t_paid=Sum('paid_amount'), t_balance=Sum('balance'))
    return qs, totals


def report_contas_cliente(customer='', status=''):
    """Open receivables grouped by customer — equiv. receberc.rpt (R11.08)."""
    qs = (
        Receivable.objects.select_related('rental__customer')
        .order_by('rental__customer__name', 'due_date')
    )
    if customer:
        qs = qs.filter(rental__customer__name__icontains=customer)
    if status == 'open':
        qs = qs.filter(balance__gt=0)
    elif status == 'paid':
        qs = qs.filter(balance__lte=0)
    # Group in Python (avoids DB-level grouping complexity with DTL)
    groups = {}
    for rec in qs:
        cust = rec.rental.customer
        if cust.pk not in groups:
            groups[cust.pk] = {
                'customer': cust,
                'receivables': [],
                'total_amount': Decimal('0'),
                'total_paid': Decimal('0'),
                'total_balance': Decimal('0'),
            }
        g = groups[cust.pk]
        g['receivables'].append(rec)
        g['total_amount'] += rec.amount
        g['total_paid'] += rec.paid_amount
        g['total_balance'] += rec.balance
    return list(groups.values())
