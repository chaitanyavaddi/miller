#!/bin/sh
# One image, two roles. The web (default) migrates, writes the recurring jobs' schedules and serves.
# The worker (`docker-entrypoint.sh worker`) waits for those migrations, then runs the job cluster.
set -e
if [ "$1" = "worker" ]; then
  until python manage.py migrate --check >/dev/null 2>&1; do echo "Waiting for migrations…"; sleep 5; done
  exec python manage.py qcluster
fi
python manage.py migrate --noinput
python manage.py ensure_schedules
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-2}" --access-logfile -
