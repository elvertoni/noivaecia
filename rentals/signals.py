from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import RentalItem


@receiver(post_save, sender=RentalItem)
@receiver(post_delete, sender=RentalItem)
def sync_rental_total(sender, instance, **kwargs):
    instance.rental.recalculate_total()
