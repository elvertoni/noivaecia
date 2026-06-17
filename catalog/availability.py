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
