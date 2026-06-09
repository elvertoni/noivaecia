FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

RUN npm run build:css
RUN npm prune --omit=dev
RUN python manage.py collectstatic --noinput 2>/dev/null || true
RUN mkdir -p /app/data /app/staticfiles \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

STOPSIGNAL SIGTERM

CMD ["./docker-entrypoint.sh"]
