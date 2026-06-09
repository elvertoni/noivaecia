"""Product availability lookup against active rentals (RF-22).

A product is considered rented on a given date when it appears in a rental item
whose rental is not yet returned and whose pickup/return window contains that date.
"""

from rentals.models import Rental, RentalItem


def find_rental_for(product, on_date):
    """Return the active rental holding ``product`` on ``on_date``, or None."""
    item = (
        RentalItem.objects.filter(
            product=product,
            rental__pickup_date__lte=on_date,
            rental__return_date__gte=on_date,
        )
        .exclude(rental__status=Rental.Status.RETURNED)
        .select_related('rental', 'rental__customer')
        .first()
    )
    return item.rental if item else None
