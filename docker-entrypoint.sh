#!/bin/sh
set -eu

echo "Waiting for database..."
python manage.py wait_for_db --timeout "${DB_WAIT_TIMEOUT:-30}"

echo "Applying database migrations..."
python manage.py migrate --noinput
# collectstatic already ran at build time (same STATIC_ROOT) — rerunning here
# only adds startup latency, since /app/staticfiles isn't a mounted volume.

# Background scheduler for Ana's daily WhatsApp report (replicas=1, so a single
# instance — no duplicate sends). Disable with WHATSAPP_SCHEDULER_ENABLED=0.
if [ "${WHATSAPP_SCHEDULER_ENABLED:-1}" = "1" ]; then
  echo "Starting WhatsApp report scheduler..."
  sh scripts/report_scheduler.sh &
fi

# Recurring DB + media backup (disable with BACKUP_SCHEDULER_ENABLED=0).
if [ "${BACKUP_SCHEDULER_ENABLED:-1}" = "1" ]; then
  echo "Starting backup scheduler..."
  sh scripts/backup_scheduler.sh &
fi

exec gunicorn noivas_cia.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-3}" \
  --timeout "${GUNICORN_TIMEOUT:-60}" \
  --access-logfile "-" \
  --error-logfile "-" \
  --capture-output \
  --log-level "${GUNICORN_LOG_LEVEL:-info}" \
  --forwarded-allow-ips "${GUNICORN_FORWARDED_ALLOW_IPS:-127.0.0.1}"
