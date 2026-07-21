"""Product availability lookup against active rentals (RF-22).

A product is considered rented on a given date when it appears in a rental item
whose rental is still active (neither returned nor cancelled) and whose
pickup/return window contains that date.
"""

from datetime import timedelta

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


def find_upcoming_pickups(product_ids, today, within_days=10):
    """Map each product id in ``product_ids`` to its nearest booked pickup.

    A product is "upcoming" when a still-pending rental has it scheduled for
    pickup within ``within_days`` days from ``today``. Used to flag pieces
    still out with the customer that are already committed to another rental
    (R10.03 devolução alert).
    """
    cutoff = today + timedelta(days=within_days)
    items = (
        RentalItem.objects.filter(
            product_id__in=product_ids,
            rental__status=Rental.Status.PENDING,
            rental__pickup_date__gte=today,
            rental__pickup_date__lte=cutoff,
        )
        .select_related('rental')
        .order_by('rental__pickup_date', 'rental__number')
    )
    result = {}
    for item in items:
        result.setdefault(item.product_id, item.rental)
    return result
