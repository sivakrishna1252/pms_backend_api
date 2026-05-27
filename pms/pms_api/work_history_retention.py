from datetime import date

from django.db.models import Q
from django.utils import timezone

from .models import Task

WORK_HISTORY_RETENTION_MONTHS = 6


def months_ago(reference: date, months: int) -> date:
    month = reference.month - months
    year = reference.year
    while month <= 0:
        month += 12
        year -= 1

    import calendar

    day = min(reference.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def work_history_cutoff_date(*, today=None):
    today = today or timezone.localdate()
    return months_ago(today, WORK_HISTORY_RETENTION_MONTHS)


def apply_work_history_retention(queryset, *, today=None):
    """Keep active tasks; hide completed tasks older than the retention window."""
    cutoff = work_history_cutoff_date(today=today)
    return queryset.filter(
        ~Q(status=Task.Status.COMPLETED) | Q(updated_at__date__gte=cutoff)
    )


def visible_completed_tasks_for_user(user, *, today=None):
    cutoff = work_history_cutoff_date(today=today)
    return Task.objects.filter(
        assigned_to=user,
        status=Task.Status.COMPLETED,
        updated_at__date__gte=cutoff,
    )
