#!/bin/sh
set -e

# Keep the scheduler unprivileged too. See entrypoint.sh for why the volume
# ownership repair is necessary during the transition from the legacy image.
if [ "$(id -u)" -eq 0 ]; then
    echo ">>> [scheduler] Ajustando permissões dos volumes persistentes..."
    # Volumes podem conter arquivos criados por uma imagem/execução anterior
    # como root; o scheduler roda como app e precisa acessá-los.
    chown -R app:app /app/data /app/media /app/staticfiles
    for sqlite_file in /app/data/db.sqlite3 /app/data/db.sqlite3-*; do
        [ -e "$sqlite_file" ] && chown app:app "$sqlite_file"
    done
    exec setpriv --reuid=app --regid=app --init-groups \
        env ENTRYPOINT_PRIVILEGES_DROPPED=1 "$0" "$@"
fi

# Entrypoint do serviço `scheduler`. Não migra nem coleta estáticos — só
# aguarda o banco e roda o processo em background (report_scheduler.sh).

echo ">>> [scheduler] Aguardando o banco de dados..."
python manage.py wait_for_db --timeout 90

exec "$@"
