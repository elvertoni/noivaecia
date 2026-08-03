"""Single source of truth for late-days and return-penalty calculation (RF-18).

Centralizing this avoids the inconsistent-penalty risk flagged in PRD section 12.

The late-return fee is **a daily percentage of what the customer owes for the
rental** — `Company.late_return_daily_rate` (10% by default) times
`rental.final_value`, for at most `Company.late_return_max_days` days (7 by
default). Confirmed with the shop on 2026-08-02.

Two earlier readings were wrong and are worth naming, because both are easy to
fall back into:

- It is **not** ``rental.penalty_value``. That field is preserved only as legacy
  import evidence; operational charges always use the current Company settings.
- It is **not** flat. The previous version applied a one-time fee precisely to
  stop a per-day multiplication from ballooning — the right instinct against the
  wrong base. With the rental value as the base and a hard day cap, per-day works
  and matches how the shop actually charges.

The cap is what keeps the two contract clauses from overlapping: up to
``late_return_max_days`` this is a late return (clause 4); past it the garment
counts as not returned and clause 6 charges ``loss_penalty_rate`` instead.
"""

from decimal import Decimal, ROUND_HALF_UP


def compute_days_late(expected_return_date, actual_return_date):
    """Whole days the return is late; never negative."""
    delta = (actual_return_date - expected_return_date).days
    return max(0, delta)


def compute_penalty(rental, days_late, company=None):
    """Late-return fee: daily rate over the rental total, capped in days.

    ``company`` is optional so callers that already loaded the singleton can pass
    it instead of paying for a second query.
    """
    if days_late <= 0:
        return Decimal('0')

    if company is None:
        from company.models import Company
        company = Company.load()

    billable_days = min(days_late, company.late_return_max_days)
    if billable_days <= 0:
        return Decimal('0')

    rate = (company.late_return_daily_rate or Decimal('0')) / Decimal('100')
    base = rental.final_value or Decimal('0')
    penalty = base * rate * Decimal(billable_days)
    return penalty.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
