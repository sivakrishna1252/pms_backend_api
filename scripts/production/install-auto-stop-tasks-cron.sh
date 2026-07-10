#!/usr/bin/env bash
# Install Mon-Sat 20:00 Asia/Kolkata cron for PMS task timer auto-stop.
set -euo pipefail

PMS_CONTAINER="${PMS_CONTAINER:-pms-web-prod}"
LOG_DIR="${LOG_DIR:-/tmp/pms}"
CRON_TAG="pms-auto-stop-tasks-8pm"
CRON_SCHEDULE="30 14 * * 1-6"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="$SCRIPT_DIR/auto-stop-tasks.sh"

chmod +x "$RUN_SCRIPT"
mkdir -p "$LOG_DIR"

filter_old_cron() {
  crontab -l 2>/dev/null \
    | grep -v "$CRON_TAG" \
    | grep -v "pms-auto-stop-running-tasks" \
    | grep -v "pms-evening-auto-stop" \
    | grep -v "pms-auto-stop-tasks" \
    | grep -v "auto-stop-tasks" \
    | grep -v "auto_stop_task_timers" \
    | grep -v "CRON_TZ=Asia/Kolkata" \
    | grep -v "auto_stop_task_timers --force" \
    || true
}

(
  filter_old_cron
  # Jenkins host cron runs in UTC. 20:00 IST = 14:30 UTC (IST is UTC+5:30).
  echo "$CRON_SCHEDULE PMS_CONTAINER=$PMS_CONTAINER LOG_DIR=$LOG_DIR $RUN_SCRIPT # $CRON_TAG"
) | crontab -

echo "Installed Mon-Sat 20:00 Asia/Kolkata ($CRON_SCHEDULE UTC) task auto-stop cron for $PMS_CONTAINER"
crontab -l | grep -E "auto-stop|$CRON_TAG" || true

if ! crontab -l | grep -q "$CRON_SCHEDULE"; then
  echo "ERROR: expected cron schedule $CRON_SCHEDULE was not installed."
  exit 1
fi

if crontab -l | grep -E "auto_stop_task_timers|auto-stop" | grep -q "\-\-force"; then
  echo "ERROR: found legacy auto-stop cron using --force. Remove it manually."
  exit 1
fi

if crontab -l | grep -E "auto_stop_task_timers|auto-stop" | grep -qE "^0 20"; then
  echo "ERROR: found legacy 0 20 UTC cron (fires at 1:30 AM IST). Remove it manually."
  exit 1
fi
