"""Single source of truth for late interest and installment generation.

Centralizing interest here addresses the incorrect-interest risk in PRD section 12.
Interest is simple per-day on the open balance using the company's configured
daily rate (RF-20): interest = balance * (daily_rate / 100) * days_late.
"""

from datetime import date as date_cls
from decimal import Decimal

from company.models import Company

from .models import Receivable


def days_overdue(receivable, on_date=None):
    """Whole days the receivable is past due (0 if paid or not yet due)."""
    if receivable.is_paid:
        return 0
    on_date = on_date or date_cls.today()
    return max(0, (on_date - receivable.due_date).days)


def compute_interest(receivable, on_date=None):
    """Late interest on the open balance using the company daily rate (RF-20)."""
    days = days_overdue(receivable, on_date)
    if days == 0:
        return Decimal('0.00')
    rate = Company.load().daily_interest_rate or Decimal('0')
    interest = receivable.balance * (rate / Decimal('100')) * days
    return interest.quantize(Decimal('0.01'))


def total_with_interest(receivable, on_date=None):
    """Open balance plus accrued late interest."""
    return (receivable.balance + compute_interest(receivable, on_date)).quantize(Decimal('0.01'))


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
