from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from core.models import TimeStampedModel


class UserManager(BaseUserManager):
    """Manager for the email-keyed custom user (no username)."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('O e-mail é obrigatório.')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superusuário precisa de is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superusuário precisa de is_superuser=True.')

        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """Application user authenticated by email instead of username."""

    email = models.EmailField('e-mail', unique=True)
    first_name = models.CharField('nome', max_length=150, blank=True)
    last_name = models.CharField('sobrenome', max_length=150, blank=True)
    is_active = models.BooleanField('ativo', default=True)
    is_staff = models.BooleanField('membro da equipe', default=False)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'usuário'
        verbose_name_plural = 'usuários'

    def __str__(self):
        return self.email

    def get_full_name(self):
        full_name = f'{self.first_name} {self.last_name}'.strip()
        return full_name or self.email

    def get_short_name(self):
        return self.first_name or self.email

    def has_module(self, module_key):
        """Return whether this user may access ``module_key``.

        Superusers always pass; other users need an allowed ModulePermission.
        """
        if self.is_superuser:
            return True
        return self.module_permissions.filter(
            module_key=module_key, allowed=True
        ).exists()

    def has_action(self, action_key):
        """Return whether this user may perform a fine-grained action (R3.11).

        Superusers always pass; other users need an allowed ActionPermission.
        """
        if self.is_superuser:
            return True
        return self.action_permissions.filter(
            action_key=action_key, allowed=True
        ).exists()

    def can_manage_users(self):
        """Return whether this user can create users and change module access."""
        if self.is_superuser:
            return True
        creator_emails = set(getattr(settings, 'USER_CREATOR_EMAILS', []))
        return self.email.lower() in creator_emails


class ModulePermission(TimeStampedModel):
    """Per-user, per-module access flag (RF-08, RF-09)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='module_permissions',
        verbose_name='usuário',
    )
    module_key = models.CharField('módulo', max_length=50)
    allowed = models.BooleanField('liberado', default=False)

    class Meta:
        verbose_name = 'permissão de módulo'
        verbose_name_plural = 'permissões de módulo'
        unique_together = ('user', 'module_key')

    def __str__(self):
        return f'{self.user} · {self.module_key} · {self.allowed}'


class ActionPermission(TimeStampedModel):
    """Fine-grained action permission beyond module-level access (R3.11).

    Action keys follow the pattern ``<module>.<action>``, e.g.
    ``customers.delete``, ``billing.receive``, ``rentals.cancel``.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='action_permissions',
        verbose_name='usuário',
    )
    action_key = models.CharField('ação', max_length=100)
    allowed = models.BooleanField('liberado', default=False)

    class Meta:
        verbose_name = 'permissão de ação'
        verbose_name_plural = 'permissões de ação'
        unique_together = ('user', 'action_key')

    def __str__(self):
        return f'{self.user} · {self.action_key} · {self.allowed}'
