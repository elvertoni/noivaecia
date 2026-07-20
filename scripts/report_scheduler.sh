#!/bin/sh
# Background scheduler for the daily WhatsApp report.
#
# Runs the management command every 30s. The command itself decides, via
# --if-due, whether it is the configured minute and whether today's report
# already went out (AuditLog), so almost every tick is a sub-second no-op.
# A 30s cadence guarantees the configured minute is always sampled at least
# once; the AuditLog idempotency guard makes a double sample harmless.
set -u

echo "[scheduler] WhatsApp daily report scheduler started"
while true; do
  if ! python manage.py send_daily_whatsapp_report --if-due; then
    echo "[scheduler] command error (ignored)"
  fi
  sleep 30
done
