FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && groupadd --gid 10001 django \
    && useradd --uid 10001 --gid django --create-home django \
    && mkdir -p /app/media /app/staticfiles \
    && chown -R django:django /app

FROM base AS development
ENV DJANGO_SETTINGS_MODULE=config.settings.development
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY --chown=django:django . .
USER django
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM base AS production
ENV DJANGO_SETTINGS_MODULE=config.settings.production
COPY --chown=django:django . .
USER django
# Build-only settings: no database connection or production secret required.
RUN DJANGO_SETTINGS_MODULE=config.settings.build python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]
