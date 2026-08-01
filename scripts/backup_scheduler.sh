#!/bin/sh
# Background scheduler for the database + media backup (golive_backup).
#
# Runs once at container startup, then every BACKUP_INTERVAL_SECONDS. Backups
# land in BACKUP_ROOT (a persistent volume) with a manifest + sha256 per run;
# --prune-days removes this command's own files older than BACKUP_RETENTION_DAYS
# so the volume doesn't grow unbounded.
set -u

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
RETENTION="${BACKUP_RETENTION_DAYS:-14}"

echo "[backup] scheduler started (every ${INTERVAL}s, keeping ${RETENTION} days)"
while true; do
  if ! python manage.py golive_backup --prune-days "$RETENTION"; then
    echo "[backup] command error (ignored)"
  fi
  sleep "$INTERVAL"
done
