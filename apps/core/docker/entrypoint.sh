#!/bin/sh
set -e

case "$1" in
  web|callback-api)
    exec php artisan serve --host=0.0.0.0 --port=8000
    ;;
  worker)
    queue="${2:?usage: worker <queue-name>}"
    exec php artisan queue:work --queue="$queue"
    ;;
  scheduler)
    exec php artisan schedule:work
    ;;
  *)
    echo "Usage: $0 {web|callback-api|worker <queue>|scheduler}" >&2
    exit 1
    ;;
esac
