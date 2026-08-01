"""DB-backed login throttle (RF-05) — see ``LoginAttempt`` for why not cache."""
from django.utils import timezone

from .models import LoginAttempt

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_MINUTES = 15

LOCKOUT_MESSAGE = (
    'Muitas tentativas de login com este e-mail. Aguarde alguns minutos e '
    'tente novamente.'
)


def _normalize(email):
    return (email or '').strip().lower()


def is_locked_out(email):
    email = _normalize(email)
    if not email:
        return False
    since = timezone.now() - timezone.timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    return LoginAttempt.objects.filter(
        email=email, created_at__gte=since,
    ).count() >= MAX_FAILED_ATTEMPTS


def record_failed_attempt(email):
    email = _normalize(email)
    if email:
        LoginAttempt.objects.create(email=email)


def clear_attempts(email):
    email = _normalize(email)
    if email:
        LoginAttempt.objects.filter(email=email).delete()
