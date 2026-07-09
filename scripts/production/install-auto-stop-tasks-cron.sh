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

(
  crontab -l 2>/dev/null | grep -v "$CRON_TAG" || true
  crontab -l 2>/dev/null | grep -v "pms-auto-stop-running-tasks" || true
  crontab -l 2>/dev/null | grep -v "pms-evening-auto-stop" || true
  crontab -l 2>/dev/null | grep -v "auto-stop-tasks.sh" || true
  echo "CRON_TZ=Asia/Kolkata"
  echo "0 20 * * 1-6 PMS_CONTAINER=$PMS_CONTAINER LOG_DIR=$LOG_DIR $RUN_SCRIPT # $CRON_TAG"
) | crontab -

echo "Installed Mon-Sat 20:00 Asia/Kolkata task auto-stop cron for $PMS_CONTAINER"
crontab -l | grep -E "CRON_TZ|auto-stop" || true
