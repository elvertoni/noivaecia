from django.conf import settings
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


class AuditLog(TimeStampedModel):
    """Immutable record of sensitive user actions (R3.10).

    created_at serves as the event timestamp — never update this model.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        verbose_name='usuário',
    )
    action = models.CharField('ação', max_length=100)
    model_name = models.CharField('modelo', max_length=100)
    object_id = models.CharField('ID do objeto', max_length=50)
    object_repr = models.CharField('representação', max_length=200)
    reason = models.TextField('motivo', blank=True)
    metadata = models.JSONField('metadados', default=dict, blank=True)

    class Meta:
        verbose_name = 'log de auditoria'
        verbose_name_plural = 'logs de auditoria'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.action} · {self.model_name}#{self.object_id} · {self.created_at:%Y-%m-%d %H:%M}'

    @classmethod
    def record(cls, *, user, action, obj, reason='', metadata=None):
        """Convenience factory: create an AuditLog for any model instance."""
        return cls.objects.create(
            user=user,
            action=action,
            model_name=obj.__class__.__name__,
            object_id=str(obj.pk),
            object_repr=str(obj)[:200],
            reason=reason,
            metadata=metadata or {},
        )
