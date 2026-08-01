"""Single source of truth for late-days and return-penalty calculation (RF-18).

Centralizing this avoids the inconsistent-penalty risk flagged in PRD section 12.

``penalty_value`` is a flat compensatory fee (cláusula penal compensatória),
matching the legacy contract's ``locado.multa``: agreed once per rental, not a
per-day rate. This was left ambiguous in the R2.07 migration policy pending
client clarification ("diária? total?") — multiplying it by days_late let a
value staff intended as a one-time fee balloon with every extra day late.
"""

from decimal import Decimal


def compute_days_late(expected_return_date, actual_return_date):
    """Whole days the return is late; never negative."""
    delta = (actual_return_date - expected_return_date).days
    return max(0, delta)


def compute_penalty(rental, days_late):
    """Flat late penalty: rental.penalty_value once if the return is late, else 0."""
    if days_late <= 0:
        return Decimal('0')
    return rental.penalty_value or Decimal('0')
