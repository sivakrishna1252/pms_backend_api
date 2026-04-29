#!/bin/sh
set -e
cd /app

# Ensure parent dir exists when DATABASE_PATH points at a mounted volume (e.g. /data/db.sqlite3).
if [ -n "${DATABASE_PATH:-}" ]; then
  dir="$(dirname "$DATABASE_PATH")"
  mkdir -p "$dir"
fi

python manage.py migrate --noinput
python manage.py ensure_superuser
exec gunicorn pms.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
