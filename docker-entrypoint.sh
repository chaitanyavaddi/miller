#!/bin/bash
# One container runs Miller: it migrates, writes the recurring jobs' schedules, then serves the web and runs
# the job worker (django-q2's qcluster) side by side. If either stops, the container exits, so the host
# (Dokploy) restarts it: a dead worker can't go unnoticed.
#
# To run the worker as its own container instead, start one with `worker` and set RUN_WORKER=0 on the web one.
set -e
if [ "$1" = "worker" ]; then
  until python manage.py migrate --check >/dev/null 2>&1; do echo "Waiting for migrations…"; sleep 5; done
  exec python manage.py qcluster
fi
python manage.py migrate --noinput
python manage.py ensure_schedules
if [ "${RUN_WORKER:-1}" = "0" ]; then
  exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-2}" --access-logfile -
fi
python manage.py qcluster &
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-2}" --access-logfile - &
trap 'kill -TERM $(jobs -p) 2>/dev/null' TERM INT  # a stop from the host stops both
wait -n
echo "A process stopped; exiting so the container restarts." >&2
kill -TERM $(jobs -p) 2>/dev/null || true
wait
exit 1
