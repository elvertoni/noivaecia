# Stage 1: build Tailwind CSS
FROM node:20-alpine AS css-builder

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY static/src/input.css static/src/input.css
COPY templates/ templates/
RUN npm run build:css

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
RUN mkdir -p /app/data /app/staticfiles /app/media \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

STOPSIGNAL SIGTERM

CMD ["./docker-entrypoint.sh"]
