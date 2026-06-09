from django.db.models.signals import post_save
from django.dispatch import receiver

from rentals.models import Rental

from .models import Pickup, Return


@receiver(post_save, sender=Pickup)
def mark_rental_picked_up(sender, instance, created, **kwargs):
    """Sync rental status to 'picked_up' when a pickup is registered (RF-17)."""
    if created:
        Rental.objects.filter(pk=instance.rental_id).update(status=Rental.Status.PICKED_UP)


@receiver(post_save, sender=Return)
def mark_rental_returned(sender, instance, created, **kwargs):
    """Sync rental status to 'returned' when a return is registered (RF-18)."""
    if created:
        Rental.objects.filter(pk=instance.rental_id).update(status=Rental.Status.RETURNED)
