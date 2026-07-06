#!/usr/bin/env bash
# Install one cron job: Mon-Sat 8 PM (Asia/Kolkata) — stop running PMS task timers.
# Jenkins runs this after deploy, or on the server:
#   chmod +x scripts/production/*.sh
#   PMS_CONTAINER=pms-web-prod scripts/production/install-auto-stop-tasks-cron.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_STOP_SCRIPT="$SCRIPT_DIR/auto-stop-tasks.sh"
chmod +x "$AUTO_STOP_SCRIPT"

PMS_CONTAINER="${PMS_CONTAINER:-pms-web-prod}"
LOG_DIR="${LOG_DIR:-/var/log/pms}"
CRON_TAG="pms-auto-stop-tasks-8pm"
CRON_TZ="${CRON_TZ:-Asia/Kolkata}"

mkdir -p "$LOG_DIR" 2>/dev/null || sudo mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="/tmp/pms"

CRON_LINE="0 20 * * 1-6 PMS_CONTAINER=$PMS_CONTAINER LOG_DIR=$LOG_DIR $AUTO_STOP_SCRIPT >> $LOG_DIR/auto-stop-tasks.log 2>&1 # $CRON_TAG"

install_user_crontab() {
  local user="${1:-$(whoami)}"
  (
    crontab -u "$user" -l 2>/dev/null | grep -v "$CRON_TAG" || true
    crontab -u "$user" -l 2>/dev/null | grep -v "pms-auto-stop-running-tasks" || true
    crontab -u "$user" -l 2>/dev/null | grep -v "pms-evening-auto-stop" || true
    echo "CRON_TZ=$CRON_TZ"
    echo "$CRON_LINE"
  ) | crontab -u "$user" -
  echo "Installed crontab for $user: Mon-Sat 20:00 $CRON_TZ"
}

install_cron_d() {
  local cron_file="/etc/cron.d/pms-auto-stop-tasks-8pm"
  sudo tee "$cron_file" >/dev/null <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
CRON_TZ=$CRON_TZ
PMS_CONTAINER=$PMS_CONTAINER
LOG_DIR=$LOG_DIR

$CRON_LINE
EOF
  sudo chmod 644 "$cron_file"
  echo "Installed $cron_file"
}

if [[ "${USE_CRON_D:-}" == "1" ]] && command -v sudo >/dev/null; then
  install_cron_d
else
  install_user_crontab "$(whoami)"
fi

echo "Logs: $LOG_DIR/auto-stop-tasks.log"
echo "Test: docker exec $PMS_CONTAINER python manage.py auto_stop_task_timers --force"
