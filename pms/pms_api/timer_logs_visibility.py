"""Hide pre-go-live timer log history in Admin UI; working time totals stay full."""

from datetime import datetime, time as dt_time

from django.conf import settings
from django.utils import timezone

from .models import Task, TimeLog


def timer_logs_visible_from_date():
    """
    First calendar date whose timer sessions are shown in admin UI.

    Uses settings.TIMER_LOGS_VISIBLE_FROM when set to hide older sessions.
    When unset, returns None and the full timer history is shown.
    """
    return getattr(settings, "TIMER_LOGS_VISIBLE_FROM", None)


def timer_logs_visible_from_datetime():
    day = timer_logs_visible_from_date()
    if day is None:
        return None
    return timezone.make_aware(datetime.combine(day, dt_time.min))


def apply_timer_logs_visibility(queryset):
    cutoff = timer_logs_visible_from_datetime()
    if cutoff is None:
        return queryset
    return queryset.filter(start_time__gte=cutoff)


def assignee_time_logs_queryset(task: Task, user_id=None):
    qs = TimeLog.objects.filter(task=task)
    if user_id:
        qs = qs.filter(user_id=user_id)
    elif task.assigned_to_id:
        qs = qs.filter(user_id=task.assigned_to_id)
    return apply_timer_logs_visibility(qs)
