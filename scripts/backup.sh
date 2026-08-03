#!/bin/bash
set -euo pipefail

# Backup do Postgres de produção e do volume de mídia, com rotação de 30 dias.
# Roda pg_dump DENTRO do container do 'db' via docker exec — contorna a
# ausência de postgresql-client na imagem do app (Dockerfile evita apt-get
# de propósito; psycopg[binary] já embute libpq).

BACKUP_DIR="${BACKUP_DIR:-/backups}"
STACK_NAME="${STACK_NAME:-noivaecia}"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Iniciando backup..."

DB_CONTAINER=$(docker ps -q -f "name=${STACK_NAME}_db" | head -n1)

if [ -z "$DB_CONTAINER" ]; then
    echo "ERRO: Container ${STACK_NAME}_db não encontrado."
    exit 1
fi

echo "[$(date)] Backup do PostgreSQL..."
docker exec "$DB_CONTAINER" pg_dump -U "${POSTGRES_USER:-noivas}" "${POSTGRES_DB:-noivas_cia}" | \
    gzip > "${BACKUP_DIR}/db_${DATE}.sql.gz"

echo "[$(date)] Backup da mídia..."
docker run --rm \
    -v "${STACK_NAME}_media_data:/data:ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine tar czf "/backup/media_${DATE}.tar.gz" -C /data .

echo "[$(date)] Rotação: removendo backups com mais de 30 dias..."
find "$BACKUP_DIR" -name "*.gz" -mtime +30 -delete 2>/dev/null || true

echo "[$(date)] Backup concluído: db_${DATE}.sql.gz e media_${DATE}.tar.gz"
