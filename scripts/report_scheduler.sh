#!/bin/sh
# Background scheduler for the daily WhatsApp report.
#
# Runs the management command every 30s. The command itself decides, via
# --if-due, whether the configured time has passed and which recipients are
# still pending. The AuditLog idempotency guard makes repeated checks harmless
# and lets a deployment or a newly added recipient catch up later in the day.
set -u

echo "[scheduler] WhatsApp daily report scheduler started"
while true; do
  if ! python manage.py send_daily_whatsapp_report --if-due; then
    echo "[scheduler] command error (ignored)"
  fi
  sleep 30
done
