"""Derive assignee timer UI state from open/closed TimeLogs (pause vs stop)."""

from __future__ import annotations

from .models import Task, TimeLog


def assignee_timer_state(task: Task) -> str | None:
    """
    For the task assignee only:
    - STARTED: open TimeLog (session running).
    - PAUSED: last closed session ended with manual pause (employee can resume).
    - STOPPED: last closed session ended with manual stop.
    - AUTO_STOPPED: last closed session ended with scheduled auto-stop (e.g. 8pm cutoff).
    - None: no TimeLog rows for this assignee on this task (not started / never tracked).
    """
    assignee_id = task.assigned_to_id
    if not assignee_id:
        return None

    if TimeLog.objects.filter(
        task=task, user_id=assignee_id, end_time__isnull=True
    ).exists():
        return "STARTED"

    last = (
        TimeLog.objects.filter(task=task, user_id=assignee_id, end_time__isnull=False)
        .order_by("-end_time")
        .first()
    )
    if last is None:
        return None

    if last.source == TimeLog.Source.MANUAL_PAUSE:
        return "PAUSED"
    if last.source == TimeLog.Source.AUTO_STOP_8PM:
        return "AUTO_STOPPED"
    return "STOPPED"
