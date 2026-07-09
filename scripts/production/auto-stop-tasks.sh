#!/usr/bin/env bash
# Mon-Sat 8:00 PM Asia/Kolkata: stop running PMS task timers and email employees.
set -euo pipefail

PMS_CONTAINER="${PMS_CONTAINER:-pms-web-prod}"
LOG_DIR="${LOG_DIR:-/tmp/pms}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/auto-stop-tasks.log"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') auto-stop start (container=$PMS_CONTAINER) ==="
  docker exec "$PMS_CONTAINER" python manage.py auto_stop_task_timers
  echo "=== done ==="
} >>"$LOG_FILE" 2>&1
