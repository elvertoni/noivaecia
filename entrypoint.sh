#!/bin/sh
set -e

# The pre-Swarm image wrote its persistent SQLite database as root. Correct
# only the mounted directories and SQLite sidecars before dropping privilege;
# Gunicorn and every Django management command below run as ``app``.
if [ "$(id -u)" -eq 0 ]; then
    echo ">>> Ajustando permissões dos volumes persistentes..."
    chown app:app /app/data /app/media /app/staticfiles
    for sqlite_file in /app/data/db.sqlite3 /app/data/db.sqlite3-*; do
        [ -e "$sqlite_file" ] && chown app:app "$sqlite_file"
    done
    exec setpriv --reuid=app --regid=app --init-groups \
        env ENTRYPOINT_PRIVILEGES_DROPPED=1 "$0" "$@"
fi

# Entrypoint do serviço web (app). O Docker Swarm ignora depends_on em
# runtime, então aguardamos o banco e aplicamos migrations de forma segura
# mesmo com várias réplicas, usando um advisory lock do PostgreSQL (só uma
# réplica migra por vez; as demais aguardam e seguem). O processo principal
# (gunicorn em produção, runserver em dev) vem do `command:` do compose/stack.

echo ">>> Aguardando o banco de dados..."
python manage.py wait_for_db --timeout 90

echo ">>> Aplicando migrations (com advisory lock para multi-réplica)..."
python <<'PY'
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'noivas_cia.settings')
django.setup()

from django.core.management import call_command
from django.db import connection

if connection.vendor == 'postgresql':
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(1)')
        acquired = cursor.fetchone()[0]
        if acquired:
            try:
                print('>>> Lock adquirido — executando migrations...')
                call_command('migrate', '--noinput')
            finally:
                cursor.execute('SELECT pg_advisory_unlock(1)')
            print('>>> Migrations concluídas e lock liberado.')
        else:
            print('>>> Outra réplica está migrando — aguardando o lock...')
            cursor.execute('SELECT pg_advisory_lock(1)')
            cursor.execute('SELECT pg_advisory_unlock(1)')
            print('>>> Migrations concluídas pela outra réplica.')
else:
    call_command('migrate', '--noinput')
PY

echo ">>> Coletando arquivos estáticos..."
python manage.py collectstatic --noinput

exec "$@"
