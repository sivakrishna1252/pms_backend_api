"""Auto-stop open (running) timers only; paused tasks are never touched."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import Task, TimeLog


def resolve_auto_stop_phase(
    now_local,
    *,
    cutoff_hour: int,
    cutoff_minute: int,
    grace_hours: int,
    force: bool = False,
):
    """
    None = before 8pm, "first" = 8pm–9pm window, "final" = 9pm+ (stop all running).
    """
    if force:
        return "final"

    cutoff = now_local.replace(
        hour=cutoff_hour, minute=cutoff_minute, second=0, microsecond=0
    )
    final_cutoff = cutoff + timedelta(hours=grace_hours)

    if now_local < cutoff:
        return None
    if now_local >= final_cutoff:
        return "final"
    return "first"


def running_time_logs_queryset():
    """Only timers still running. Paused tasks already closed their TimeLog."""
    return TimeLog.objects.filter(end_time__isnull=True).select_related("task", "user")


def last_activity_local(log: TimeLog):
    return timezone.localtime(log.last_activity_at or log.start_time)


def has_recent_activity_near_cutoff(log: TimeLog, cutoff_dt, grace_hours: int) -> bool:
    """
    True when the employee was still active within grace_hours before cutoff (e.g. 7–8pm).
    Those running timers are left until the 9pm final pass.
    """
    activity = last_activity_local(log)
    grace_start = cutoff_dt - timedelta(hours=grace_hours)
    return activity >= grace_start


def should_auto_stop_at_first_pass(log: TimeLog, cutoff_dt, grace_hours: int) -> bool:
    """
    8pm pass: stop forgotten running timers (no recent activity near cutoff).
    Skip running timers that were active near 8pm — handled at 9pm.
    """
    return not has_recent_activity_near_cutoff(log, cutoff_dt, grace_hours)


def auto_stop_time_log(log: TimeLog, *, sync_task_status: bool = True) -> None:
    log.stop(source=TimeLog.Source.AUTO_STOP_8PM)
    if not sync_task_status:
        return
    task = log.task
    if task.status == Task.Status.IN_PROGRESS:
        task.status = Task.Status.PAUSED
        task.save(update_fields=["status"])
