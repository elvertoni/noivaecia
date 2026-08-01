# Stage 1: build Tailwind CSS
FROM node:20-alpine AS css-builder

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY tailwind.config.js tailwind.config.js
COPY static/src/input.css static/src/input.css
COPY templates/ templates/
RUN npm run build:css
# Fail loudly if the build produced no usable stylesheet.
RUN test -s static/css/output.css

# Stage 2: Python runtime (psycopg[binary] embute libpq — apt só entra para o
# pg_dump usado pelo backup automático; instalamos o client 16 via PGDG para
# casar com a versão do servidor de produção, pg_dump mais velho que o
# servidor pode falhar em dumps).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=css-builder /app/static/css/ static/css/

RUN DJANGO_SECRET_KEY=build-time-only DJANGO_ALLOWED_HOSTS=localhost python manage.py collectstatic --noinput
RUN mkdir -p /app/data /app/staticfiles /app/media \
    && chmod +x /app/docker-entrypoint.sh

# Non-root runtime user. NOTE: on an existing deploy, /app/data and /app/media
# are named volumes already populated with files owned by root (every prior
# release ran as root) — switching USER here without first chowning those
# volumes on the host/container breaks writes (backups, uploaded photos,
# sqlite fallback). Run once against the live volumes before/at this rollout:
#   docker exec -u root <container> chown -R appuser:appuser /app/data /app/media
RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --no-create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

STOPSIGNAL SIGTERM

CMD ["./docker-entrypoint.sh"]
