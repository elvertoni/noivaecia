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

# Stage 2: Python runtime (sem apt-get — psycopg[binary] embute libpq)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=css-builder /app/static/css/ static/css/

RUN DJANGO_SECRET_KEY=build-time-only DJANGO_ALLOWED_HOSTS=localhost python manage.py collectstatic --noinput
RUN chmod +x /app/entrypoint.sh /app/worker-entrypoint.sh

RUN groupadd -r app && useradd -r -g app -d /app app \
    && mkdir -p /app/data /app/staticfiles /app/media \
    && chown -R app:app /app

# The entrypoints start as root only long enough to repair ownership on volumes
# created by the legacy root-based image, then immediately re-exec as ``app``.
# This keeps the running web and scheduler processes unprivileged while making
# the upgrade safe for the existing persistent SQLite volume.
USER root

EXPOSE 8000

STOPSIGNAL SIGTERM

ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "noivas_cia.wsgi:application", "--bind", "0.0.0.0:8000"]
