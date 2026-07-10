#!/usr/bin/env bash
# Install Mon-Sat 20:00 Asia/Kolkata cron for PMS task timer auto-stop.
set -euo pipefail

PMS_CONTAINER="${PMS_CONTAINER:-pms-web-prod}"
LOG_DIR="${LOG_DIR:-/tmp/pms}"
CRON_TAG="pms-auto-stop-tasks-8pm"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="$SCRIPT_DIR/auto-stop-tasks.sh"

chmod +x "$RUN_SCRIPT"
mkdir -p "$LOG_DIR"

filter_old_cron() {
  crontab -l 2>/dev/null \
    | grep -v "$CRON_TAG" \
    | grep -v "pms-auto-stop-running-tasks" \
    | grep -v "pms-evening-auto-stop" \
    | grep -v "auto-stop-tasks" \
    | grep -v "auto_stop_task_timers" \
    | grep -v "CRON_TZ=Asia/Kolkata" \
    || true
}

(
  filter_old_cron
  # Jenkins/host cron uses UTC. 20:00 IST = 14:30 UTC (IST is UTC+5:30).
  echo "30 14 * * 1-6 PMS_CONTAINER=$PMS_CONTAINER LOG_DIR=$LOG_DIR $RUN_SCRIPT # $CRON_TAG"
) | crontab -

echo "Installed Mon-Sat 20:00 Asia/Kolkata (14:30 UTC) task auto-stop cron for $PMS_CONTAINER"
crontab -l | grep -E "auto-stop|$CRON_TAG" || true
