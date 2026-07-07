"""Auto-stop open (running) timers only; paused tasks are never touched."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from .models import Task, TimeLog


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
    """
    Stop every running TimeLog (8 PM cron job). Returns {email: {name, tasks: [titles]}}.
    """
    stopped_by_user: dict[str, dict] = {}
    for log in running_time_logs_queryset():
        auto_stop_time_log(log, sync_task_status=sync_task_status)
        if on_task_sync:
            on_task_sync(log.task)
        _collect_stopped_by_user(log, stopped_by_user)
    return stopped_by_user
