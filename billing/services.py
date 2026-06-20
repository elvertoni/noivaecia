"""Single source of truth for late interest and installment generation.

Centralizing interest here addresses the incorrect-interest risk in PRD section 12.
Interest is simple per-day on the open balance using the company's configured
daily rate (RF-20): interest = balance * (daily_rate / 100) * days_late.
"""

from datetime import date as date_cls
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce

from company.models import Company

from .models import CashAccount, FinancialMovement, Payment, Receivable


def days_overdue(receivable, on_date=None):
    """Whole days the receivable is past due (0 if paid or not yet due)."""
    if receivable.is_paid:
        return 0
    on_date = on_date or date_cls.today()
    return max(0, (on_date - receivable.due_date).days)


def compute_interest(receivable, on_date=None, company=None):
    """Late interest on the open balance using the company daily rate (RF-20)."""
    days = days_overdue(receivable, on_date)
    if days == 0:
        return Decimal('0.00')
    company = company or Company.load()
    rate = company.daily_interest_rate or Decimal('0')
    interest = receivable.balance * (rate / Decimal('100')) * days
    return interest.quantize(Decimal('0.01'))


def total_with_interest(receivable, on_date=None, company=None):
    """Open balance plus accrued late interest."""
    return (
        receivable.balance + compute_interest(receivable, on_date, company=company)
    ).quantize(Decimal('0.01'))


def interest_breakdown(receivable, on_date=None, company=None):
    """Return days, interest, and total using one company config lookup."""
    interest = compute_interest(receivable, on_date, company=company)
    return {
        'interest': interest,
        'total_with_interest': (receivable.balance + interest).quantize(Decimal('0.01')),
        'days_overdue': days_overdue(receivable, on_date),
    }


def generate_for_rental(rental, installments=1, first_due_date=None):
    """Create receivables splitting the rental total into N installments (RF-19/8.1.3).

    The total is divided evenly; any rounding remainder lands on the first
    installment so the sum matches the rental total exactly. Installments fall
    due monthly starting at ``first_due_date`` (defaults to the return date).
    """
    installments = max(1, int(installments))
    first_due_date = first_due_date or rental.return_date
    total = rental.total_value or Decimal('0')

    base = (total / installments).quantize(Decimal('0.01'))
    remainder = total - base * installments

    created = []
    for index in range(installments):
        amount = base + (remainder if index == 0 else Decimal('0'))
        due = _add_months(first_due_date, index)
        created.append(
            Receivable.objects.create(rental=rental, due_date=due, amount=amount)
        )
    return created


def _add_months(start, months):
    """Add ``months`` to a date, clamping the day to the target month length."""
    from calendar import monthrange

    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    last_day = monthrange(year, month)[1]
    return date_cls(year, month, min(start.day, last_day))


def register_payment(receivable, amount, payment_date, method='cash',
                     interest_amount=None, discount_amount=None, notes='', user=None):
    """Create Payment, recalculate receivable balance, create FinancialMovement inflow (R5.06)."""
    amount = Decimal(str(amount))
    interest_amount = Decimal(str(interest_amount or 0))
    discount_amount = Decimal(str(discount_amount or 0))

    with transaction.atomic():
        locked_receivable = (
            Receivable.objects.select_for_update()
            .select_related('rental__customer')
            .get(pk=receivable.pk)
        )
        payment = Payment.objects.create(
            receivable=locked_receivable,
            customer=locked_receivable.rental.customer,
            rental=locked_receivable.rental,
            payment_date=payment_date,
            amount=amount,
            interest_amount=interest_amount,
            discount_amount=discount_amount,
            method=method,
            notes=notes,
            user=user,
        )
        locked_receivable.recalculate_from_payments()

        account = CashAccount.objects.select_for_update().filter(active=True).order_by('id').first()
        if account:
            FinancialMovement.objects.create(
                date=payment_date,
                account=account,
                direction=FinancialMovement.Direction.INFLOW,
                amount=amount,
                description=f'Recebimento — Locação #{locked_receivable.rental.number}',
                source=FinancialMovement.Source.PAYMENT,
                customer=locked_receivable.rental.customer,
                receivable=locked_receivable,
                payment=payment,
                rental=locked_receivable.rental,
                created_by=user,
            )
    return payment


def reverse_payment(payment, reason, user=None):
    """Create reversal Payment (negative amount) and FinancialMovement outflow (R5.09)."""
    today = date_cls.today()
    with transaction.atomic():
        locked_payment = (
            Payment.objects.select_for_update()
            .select_related('receivable', 'customer', 'rental')
            .get(pk=payment.pk)
        )
        if locked_payment.is_reversal or locked_payment.reversed_by_id is not None:
            raise ValueError('Este pagamento já foi estornado.')

        receivable = (
            Receivable.objects.select_for_update()
            .select_related('rental__customer')
            .get(pk=locked_payment.receivable_id)
        )
        reversal = Payment.objects.create(
            receivable=receivable,
            customer=locked_payment.customer,
            rental=locked_payment.rental,
            payment_date=today,
            amount=-locked_payment.amount,
            interest_amount=-locked_payment.interest_amount,
            discount_amount=-locked_payment.discount_amount,
            method=locked_payment.method,
            notes=f'Estorno: {reason}',
            user=user,
            is_reversal=True,
        )
        locked_payment.reversed_by = reversal
        locked_payment.save(update_fields=['reversed_by', 'updated_at'])
        receivable.recalculate_from_payments()

        account = CashAccount.objects.select_for_update().filter(active=True).order_by('id').first()
        if account:
            FinancialMovement.objects.create(
                date=today,
                account=account,
                direction=FinancialMovement.Direction.OUTFLOW,
                amount=locked_payment.amount,
                description=(
                    f'Estorno pgto #{locked_payment.pk} — '
                    f'Locação #{locked_payment.rental.number if locked_payment.rental else "?"}'
                ),
                source=FinancialMovement.Source.REVERSAL,
                customer=locked_payment.customer,
                receivable=receivable,
                payment=reversal,
                rental=locked_payment.rental,
                created_by=user,
            )
    return reversal


def compute_moratoria(receivable, on_date=None, company=None):
    """Late moratoria fee (multa moratória) on the open balance using Company.late_fee_rate (R6.09).

    Applied once when the receivable becomes overdue (not per day).
    Returns zero if paid or not overdue.
    """
    if receivable.is_paid:
        return Decimal('0.00')
    on_date = on_date or date_cls.today()
    if on_date <= receivable.due_date:
        return Decimal('0.00')
    company = company or Company.load()
    rate = company.late_fee_rate or Decimal('0')
    fee = receivable.balance * (rate / Decimal('100'))
    return fee.quantize(Decimal('0.01'))


def compute_monthly_interest(receivable, on_date=None, company=None):
    """Monthly interest (juros ao mês) using Company.monthly_interest_rate, applied daily (R6.09).

    Uses monthly_rate/30 per day. Falls back to daily_interest_rate if monthly is zero.
    """
    days = days_overdue(receivable, on_date)
    if days == 0:
        return Decimal('0.00')
    company = company or Company.load()
    monthly_rate = company.monthly_interest_rate or Decimal('0')
    if monthly_rate:
        daily = monthly_rate / Decimal('30')
    else:
        daily = company.daily_interest_rate or Decimal('0')
    interest = receivable.balance * (daily / Decimal('100')) * days
    return interest.quantize(Decimal('0.01'))


def compute_damage_penalty(item_value, company=None):
    """Damage penalty: Company.damage_penalty_rate % of item value (R6.09)."""
    company = company or Company.load()
    rate = company.damage_penalty_rate or Decimal('0')
    return (Decimal(str(item_value)) * rate / Decimal('100')).quantize(Decimal('0.01'))


def compute_loss_penalty(item_value, company=None):
    """Loss/non-return penalty: Company.loss_penalty_rate % of item value (R6.09)."""
    company = company or Company.load()
    rate = company.loss_penalty_rate or Decimal('0')
    return (Decimal(str(item_value)) * rate / Decimal('100')).quantize(Decimal('0.01'))


def reconcile_financial():
    """Compare receivables, payments, balances and movements. Return dict of aggregates (R6.05)."""
    from .models import FinancialMovement, Payment, Receivable

    total_receivable_amount = (
        Receivable.objects.aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    total_open_balance = (
        Receivable.objects.filter(balance__gt=0).aggregate(v=Sum('balance'))['v'] or Decimal('0')
    )
    total_payments = (
        Payment.objects.filter(is_reversal=False).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    total_reversals = (
        Payment.objects.filter(is_reversal=True).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    total_inflow = (
        FinancialMovement.objects.filter(direction=FinancialMovement.Direction.INFLOW)
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    total_outflow = (
        FinancialMovement.objects.filter(direction=FinancialMovement.Direction.OUTFLOW)
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )

    # Divergence 1: paid receivables (balance <= 0) with no Payment records
    # These are legacy-imported receivables forced closed by pago=0 logic
    paid_no_payments = Receivable.objects.filter(balance__lte=0).exclude(payments__isnull=False)
    paid_no_payments_count = paid_no_payments.count()
    paid_no_payments_sum = (
        paid_no_payments.aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )

    # Divergence 2: open receivables where paid_amount != sum(payments.amount)
    inconsistent_qs = (
        Receivable.objects.filter(balance__gt=0)
        .select_related('rental')
        .annotate(
            payment_sum=Coalesce(
                Sum('payments__amount'),
                Value(Decimal('0')),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )
        .exclude(paid_amount=F('payment_sum'))
    )
    inconsistent_count = inconsistent_qs.count()
    inconsistent_balances = []
    for rec in inconsistent_qs[:100]:
        if abs(rec.paid_amount - rec.payment_sum) > Decimal('0.01'):
            inconsistent_balances.append({
                'id': rec.pk,
                'rental_number': rec.rental.number if rec.rental_id else None,
                'due_date': rec.due_date,
                'amount': rec.amount,
                'paid_amount_stored': rec.paid_amount,
                'payment_sum': rec.payment_sum,
                'diff': rec.paid_amount - rec.payment_sum,
            })

    # Divergence 3: payments without a corresponding FinancialMovement
    payments_without_movement = (
        Payment.objects.filter(is_reversal=False)
        .exclude(financial_movements__source=FinancialMovement.Source.PAYMENT)
        .distinct()
    )
    payments_without_movement_count = payments_without_movement.count()
    payments_without_movement_ids = list(
        payments_without_movement.values_list('pk', flat=True)[:200]
    )

    return {
        'total_receivable_amount': total_receivable_amount,
        'total_open_balance': total_open_balance,
        'total_payments': total_payments,
        'total_reversals': abs(total_reversals),
        'net_payments': total_payments + total_reversals,
        'total_inflow': total_inflow,
        'total_outflow': total_outflow,
        'net_movements': total_inflow - total_outflow,
        'paid_no_payments_count': paid_no_payments_count,
        'paid_no_payments_sum': paid_no_payments_sum,
        'inconsistent_balances': inconsistent_balances,
        'inconsistent_count': inconsistent_count,
        'payments_without_movement_count': payments_without_movement_count,
    }


def financial_kpis(today=None):
    """KPIs for the billing dashboard (R5.02)."""
    from datetime import date as date_cls, timedelta
    from django.db.models import Sum
    from .models import FinancialMovement, Payment, Receivable

    today = today or date_cls.today()
    week_end = today + timedelta(days=7)
    month_start = today.replace(day=1)

    open_qs = Receivable.objects.filter(balance__gt=0)
    overdue_qs = open_qs.filter(due_date__lt=today)
    due_today_qs = open_qs.filter(due_date=today)
    due_week_qs = open_qs.filter(due_date__gt=today, due_date__lte=week_end)

    open_balance = open_qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')
    overdue_balance = overdue_qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')
    due_today_balance = due_today_qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')
    due_week_balance = due_week_qs.aggregate(v=Sum('balance'))['v'] or Decimal('0')

    received_today = (
        Payment.objects.filter(payment_date=today, is_reversal=False)
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )
    received_month = (
        Payment.objects.filter(payment_date__gte=month_start, is_reversal=False)
        .aggregate(v=Sum('amount'))['v'] or Decimal('0')
    )

    recent_movements = (
        FinancialMovement.objects.select_related('account', 'customer')
        .order_by('-date', '-created_at')[:10]
    )

    return {
        'open_balance': open_balance,
        'open_count': open_qs.count(),
        'overdue_balance': overdue_balance,
        'overdue_count': overdue_qs.count(),
        'due_today_balance': due_today_balance,
        'due_today_count': due_today_qs.count(),
        'due_week_balance': due_week_balance,
        'due_week_count': due_week_qs.count(),
        'received_today': received_today,
        'received_month': received_month,
        'recent_movements': recent_movements,
        'today': today,
    }
