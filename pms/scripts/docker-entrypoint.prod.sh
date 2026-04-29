#!/bin/sh
set -e
cd /app
python manage.py migrate --noinput
python manage.py ensure_superuser
exec gunicorn pms.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
