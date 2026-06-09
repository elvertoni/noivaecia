from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base model adding self-managed creation and update timestamps.

    Every concrete model in the project inherits from this so all tables
    expose 'created_at' and 'updated_at' consistently.
    """

    created_at = models.DateTimeField('criado em', auto_now_add=True)
    updated_at = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        abstract = True
