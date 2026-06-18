"""Product availability lookup against active rentals (RF-22).

A product is considered rented on a given date when it appears in a rental item
whose rental is still active (neither returned nor cancelled) and whose
pickup/return window contains that date.
"""

from rentals.models import Rental, RentalItem

# A rental no longer holds its items once it is returned or cancelled.
INACTIVE_RENTAL_STATUSES = (Rental.Status.RETURNED, Rental.Status.CANCELLED)


def find_rental_for(product, on_date):
    """Return the active rental holding ``product`` on ``on_date``, or None."""
    item = (
        RentalItem.objects.filter(
            product=product,
            rental__pickup_date__lte=on_date,
            rental__return_date__gte=on_date,
        )
        .exclude(rental__status__in=INACTIVE_RENTAL_STATUSES)
        .select_related('rental', 'rental__customer')
        # Deterministic pick if a piece is (erroneously) in two active rentals.
        .order_by('rental__return_date', 'rental__number')
        .first()
    )
    return item.rental if item else None


def find_overlapping_rental(product, pickup_date, return_date, exclude_rental_id=None):
    """Return an active rental whose window overlaps [pickup_date, return_date].

    Two date ranges overlap when each starts on or before the other ends. Used
    to block double-booking a product into two simultaneous contracts (R7.04).
    ``exclude_rental_id`` skips the rental being edited so it never conflicts
    with itself.
    """
    qs = (
        RentalItem.objects.filter(
            product=product,
            rental__pickup_date__lte=return_date,
            rental__return_date__gte=pickup_date,
        )
        .exclude(rental__status__in=INACTIVE_RENTAL_STATUSES)
        .select_related('rental', 'rental__customer')
        .order_by('rental__pickup_date', 'rental__number')
    )
    if exclude_rental_id:
        qs = qs.exclude(rental_id=exclude_rental_id)
    item = qs.first()
    return item.rental if item else None
