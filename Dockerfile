# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod PYTHONPATH=/app/src
WORKDIR /app

COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY manage.py pyproject.toml ./
COPY src ./src
COPY scripts ./scripts

# Build-time only: collectstatic needs a key, never persisted in the image environment.
RUN DJANGO_SECRET_KEY=build-only DATABASE_URL=postgres://u:p@localhost/db python manage.py collectstatic --noinput

RUN useradd --system --uid 10001 --home /app app && mkdir -p /app/media /app/.gunicorn && chown -R app /app/media /app/staticfiles /app/.gunicorn
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health/live/', timeout=3)" || exit 1
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-", "--forwarded-allow-ips", "*"]
