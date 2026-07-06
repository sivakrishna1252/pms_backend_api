#!/usr/bin/env bash
# Mon-Sat 8 PM (Asia/Kolkata): stop all running PMS task timers.
# Install: scripts/production/install-auto-stop-tasks-cron.sh
# Test:    docker exec pms-web-prod python manage.py auto_stop_task_timers --force

set -euo pipefail

PMS_CONTAINER="${PMS_CONTAINER:-pms-web-prod}"
LOG_DIR="${LOG_DIR:-/var/log/pms}"
LOG_FILE="${LOG_DIR}/auto-stop-tasks.log"

mkdir -p "$LOG_DIR" 2>/dev/null || true

log() {
  local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$line"
  echo "$line" >>"$LOG_FILE" 2>/dev/null || true
}

if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$PMS_CONTAINER"; then
  log "ERROR: container '$PMS_CONTAINER' is not running."
  exit 1
fi

log "Task auto-stop starting (container=$PMS_CONTAINER)..."
docker exec "$PMS_CONTAINER" python manage.py auto_stop_task_timers --force
log "Task auto-stop done."
