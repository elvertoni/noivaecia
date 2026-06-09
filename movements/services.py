"""Single source of truth for late-days and return-penalty calculation (RF-18).

Centralizing this avoids the inconsistent-penalty risk flagged in PRD section 12.
The late penalty uses the rental's ``penalty_value`` as a per-day late fee.
"""

from decimal import Decimal


def compute_days_late(expected_return_date, actual_return_date):
    """Whole days the return is late; never negative."""
    delta = (actual_return_date - expected_return_date).days
    return max(0, delta)


def compute_penalty(rental, days_late):
    """Late penalty = days_late * rental.penalty_value (per-day late fee)."""
    return (rental.penalty_value or Decimal('0')) * days_late
