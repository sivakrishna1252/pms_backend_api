"""Hide pre-go-live timer log history in Admin UI; working time totals stay full."""

from datetime import datetime, time as dt_time

from django.conf import settings
from django.utils import timezone

from .models import Task, TimeLog


def timer_logs_visible_from_date():
    """
    First calendar date whose timer sessions are shown in admin UI.

    Uses settings.TIMER_LOGS_VISIBLE_FROM when set (recommended on deploy).
    Otherwise falls back to the current local date so older test data stays hidden.
    """
    configured = getattr(settings, "TIMER_LOGS_VISIBLE_FROM", None)
    if configured is not None:
        return configured
    return timezone.localdate()


def timer_logs_visible_from_datetime():
    day = timer_logs_visible_from_date()
    return timezone.make_aware(datetime.combine(day, dt_time.min))


def apply_timer_logs_visibility(queryset):
    return queryset.filter(start_time__gte=timer_logs_visible_from_datetime())


def assignee_time_logs_queryset(task: Task, user_id=None):
    qs = TimeLog.objects.filter(task=task)
    if user_id:
        qs = qs.filter(user_id=user_id)
    elif task.assigned_to_id:
        qs = qs.filter(user_id=task.assigned_to_id)
    return apply_timer_logs_visibility(qs)
