"""Auto-stop open (running) timers once per Mon-Sat evening (default 8 PM)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Task, TaskEveningAutoStopRun, TimeLog


def running_time_logs_queryset():
    """Only timers still running. Paused tasks already closed their TimeLog."""
    return TimeLog.objects.filter(end_time__isnull=True).select_related("task", "user")


def auto_stop_cutoff_local(now_local=None):
    now_local = now_local or timezone.localtime()
    hour = int(getattr(settings, "AUTO_STOP_CUTOFF_HOUR", 20))
    minute = int(getattr(settings, "AUTO_STOP_CUTOFF_MINUTE", 0))
    return now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def is_past_auto_stop_cutoff(now_local=None) -> bool:
    """Mon-Sat at or after the configured evening cutoff (default 8:00 PM local)."""
    now_local = now_local or timezone.localtime()
    if now_local.weekday() > 5:
        return False
    return now_local >= auto_stop_cutoff_local(now_local)


def is_evening_auto_stop_window(now_local=None) -> bool:
    """Mon-Sat from a few minutes before cutoff through end of local day."""
    now_local = now_local or timezone.localtime()
    if now_local.weekday() > 5:
        return False
    cutoff = auto_stop_cutoff_local(now_local)
    earliest = cutoff - timedelta(minutes=5)
    end_of_day = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
    return earliest <= now_local <= end_of_day


def needs_stale_timer_catchup(now_local=None) -> bool:
    """Running timers that started on a prior calendar day (missed evening pass)."""
    now_local = now_local or timezone.localtime()
    today = now_local.date()
    return running_time_logs_queryset().filter(start_time__date__lt=today).exists()


def is_auto_stop_allowed_now(now_local=None, *, force: bool = False) -> bool:
    """
    When cron/host timezone is wrong, refuse to auto-stop or email outside allowed hours.
    - Evening window: ~8 PM through midnight (Mon-Sat).
    - Daytime catch-up: 8 AM until cutoff for multi-day stale timers only.
    --force bypasses this (manual testing only; never use in production cron).
    """
    if force:
        return True
    now_local = now_local or timezone.localtime()
    if now_local.weekday() > 5:
        return False
    cutoff_hour = int(getattr(settings, "AUTO_STOP_CUTOFF_HOUR", 20))
    daytime_catchup = (
        8 <= now_local.hour < cutoff_hour and needs_stale_timer_catchup(now_local)
    )
    return is_evening_auto_stop_window(now_local) or daytime_catchup


def _cutoff_for_date(run_date, tzinfo):
    hour = int(getattr(settings, "AUTO_STOP_CUTOFF_HOUR", 20))
    minute = int(getattr(settings, "AUTO_STOP_CUTOFF_MINUTE", 0))
    return datetime.combine(run_date, time(hour=hour, minute=minute), tzinfo=tzinfo)


def pending_evening_run_dates(now_local=None) -> list:
    """
    Mon-Sat evening passes not yet recorded for the last week.
    Includes catch-up when cron missed but timers are still running from prior days.
    """
    now_local = now_local or timezone.localtime()
    today = now_local.date()
    tzinfo = now_local.tzinfo
    pending: list = []
    has_stale_running = running_time_logs_queryset().filter(start_time__date__lt=today).exists()

    for days_back in range(6, -1, -1):
        run_date = today - timedelta(days=days_back)
        if run_date.weekday() > 5:
            continue
        if TaskEveningAutoStopRun.objects.filter(run_date=run_date).exists():
            continue
        cutoff = _cutoff_for_date(run_date, tzinfo)
        if now_local >= cutoff:
            pending.append(run_date)
        elif run_date < today and has_stale_running:
            pending.append(run_date)

    return pending


def mark_evening_runs_completed(run_dates) -> None:
    for run_date in run_dates:
        TaskEveningAutoStopRun.objects.get_or_create(run_date=run_date)


def auto_stop_time_log(log: TimeLog, *, sync_task_status: bool = True) -> None:
    log.stop(source=TimeLog.Source.AUTO_STOP_8PM)
    if not sync_task_status:
        return
    task = log.task
    if task.status == Task.Status.IN_PROGRESS:
        task.status = Task.Status.PAUSED
        task.save(update_fields=["status"])


def _collect_stopped_by_user(log: TimeLog, stopped_by_user: dict[str, dict]) -> None:
    user_email = getattr(log.user, "email", None)
    if not user_email:
        return
    stopped_by_user.setdefault(
        user_email,
        {
            "name": log.user.get_full_name().strip() or log.user.username,
            "tasks": [],
        },
    )
    stopped_by_user[user_email]["tasks"].append(log.task.title)


def auto_stop_all_running_timers(*, sync_task_status: bool = True, on_task_sync=None):
    """Stop every running TimeLog. Returns {email: {name, tasks: [titles]}}."""
    stopped_by_user: dict[str, dict] = {}
    for log in running_time_logs_queryset():
        auto_stop_time_log(log, sync_task_status=sync_task_status)
        if on_task_sync:
            on_task_sync(log.task)
        _collect_stopped_by_user(log, stopped_by_user)
    return stopped_by_user


def send_evening_auto_stop_emails(stopped_by_user: dict[str, dict]) -> None:
    if not stopped_by_user:
        return
    from pms_api.views import _send_styled_email

    cutoff_hour = int(getattr(settings, "AUTO_STOP_CUTOFF_HOUR", 20))
    cutoff_minute = int(getattr(settings, "AUTO_STOP_CUTOFF_MINUTE", 0))
    cutoff_label = f"{cutoff_hour:02d}:{cutoff_minute:02d}"
    for email, payload in stopped_by_user.items():
        task_lines = ", ".join(payload["tasks"]) or "N/A"
        _send_styled_email(
            subject=f"PMS: Task timer auto-stopped at {cutoff_label}",
            recipient_list=[email],
            greeting=f"Hi {payload['name']},",
            intro_text=(
                f"You forgot to stop your task timer(s) before {cutoff_label}. "
                "PMS has auto-stopped them for you."
            ),
            detail_rows=[
                ("Auto-stopped tasks", task_lines),
                (
                    "Reminder",
                    "Please stop or pause your timer when you finish work. "
                    "Do not forget again — paused tasks are never auto-stopped.",
                ),
            ],
        )


def run_evening_auto_stop_if_due(*, force: bool = False, notify: bool = True, on_task_sync=None):
    """
    Run the evening pass once per Mon-Sat date:
    - at/after 8 PM via cron, or
    - catch-up on next API use if cron missed (stale running timers only).
    After the pass runs for a date, timers started later that evening are left alone.
    """
    now_local = timezone.localtime()
    if not force and now_local.weekday() > 5:
        return {}

    if not is_auto_stop_allowed_now(now_local, force=force):
        return {}

    pending_dates = pending_evening_run_dates(now_local)
    if not pending_dates and not force:
        return {}

    if force and not running_time_logs_queryset().exists():
        return {}

    stopped_by_user = auto_stop_all_running_timers(
        sync_task_status=True,
        on_task_sync=on_task_sync,
    )

    if pending_dates:
        mark_evening_runs_completed(pending_dates)
    elif force:
        if now_local.weekday() <= 5:
            mark_evening_runs_completed([now_local.date()])

    if notify and stopped_by_user:
        send_evening_auto_stop_emails(stopped_by_user)

    return stopped_by_user
