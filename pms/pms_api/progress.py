"""Work-tracking progress for tasks, milestones, and projects.

Each task has a *planned* duration (hours) and *worked* time from the timer.

- Planned hours: `Task.estimated_hours` when > 0; otherwise inclusive calendar days
  from `created_at` to the best available end anchor × 8 h/day:
  `task.deadline`, else milestone `end_date`, else project `deadline`.

- Worked hours: Same source as Admin **Work tracking** — sum of completed
  `TimeLog.duration_seconds` for the assignee, plus active session wall time.

  (Using `total_time_spent_seconds` alone can diverge from the work-tracking
  report; we reconcile with logs and fall back to the task field.)

Progress rules:
- NOT_STARTED → 0% only when worked time is 0 (if time exists but status is stale,
  still compute from time).
- COMPLETED → 100%
- Otherwise → worked / planned × 100, capped at 95% until completed.

Rollup: weighted average by planned hours over tasks that have planned > 0.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Milestone, Project, Task, TimeLog

WORKING_HOURS_PER_DAY = Decimal("8")
INCOMPLETE_MAX_PERCENT = Decimal("95")
_MAX_PROGRESS_OUTPUT = Decimal("0.01")


def _quantize_percent(value: Decimal) -> float:
    return float((value.quantize(_MAX_PROGRESS_OUTPUT)))


def _planned_end_anchor_date(task: Task):
    """Date used with task created_at for inclusive day span × 8h."""
    if task.deadline is not None:
        return task.deadline
    m = getattr(task, "milestone", None)
    if m is not None and getattr(m, "end_date", None):
        return m.end_date
    p = getattr(task, "project", None)
    if p is not None and getattr(p, "deadline", None):
        return p.deadline
    return None


def planned_hours_for_task(task: Task) -> Decimal:
    """Return planned effort in hours, or 0 if it cannot be derived."""
    est = task.estimated_hours
    if est is not None and est > 0:
        return Decimal(est)

    end = _planned_end_anchor_date(task)
    if end is None:
        return Decimal("0")

    if task.created_at:
        start = timezone.localdate(task.created_at)
    else:
        start = timezone.localdate()

    days = (end - start).days + 1
    if days < 1:
        days = 1
    return Decimal(days) * WORKING_HOURS_PER_DAY


def effective_worked_seconds(task: Task, now=None) -> int:
    """Authoritative worked time aligned with Admin work-tracking totals."""
    now = now or timezone.now()
    assignee_id = task.assigned_to_id
    if not assignee_id:
        return int(task.total_time_spent_seconds or 0)

    closed_sum = (
        TimeLog.objects.filter(
            task_id=task.id,
            user_id=assignee_id,
            end_time__isnull=False,
        ).aggregate(s=Sum("duration_seconds"))["s"]
        or 0
    )
    base = int(closed_sum)
    log = (
        TimeLog.objects.filter(task_id=task.id, user_id=assignee_id, end_time__isnull=True)
        .order_by("-start_time")
        .first()
    )
    if log:
        base += int((now - log.start_time).total_seconds())
    # Legacy / drift fallback if logs were incomplete but denormalised field populated
    if base == 0:
        base = int(task.total_time_spent_seconds or 0)
    return base


def task_progress_percent(task: Task, now=None) -> float:
    """Single task progress 0–100 for API display."""
    now = now or timezone.now()
    if task.status == Task.Status.COMPLETED:
        return 100.0

    planned = planned_hours_for_task(task)
    if planned <= 0:
        return 0.0

    worked_sec = effective_worked_seconds(task, now)
    if worked_sec <= 0 and task.status == Task.Status.NOT_STARTED:
        return 0.0

    worked_h = Decimal(worked_sec) / Decimal(3600)
    raw = (worked_h / planned) * Decimal(100)
    if raw >= Decimal(100):
        return _quantize_percent(INCOMPLETE_MAX_PERCENT)
    return _quantize_percent(raw)


def work_tracking_progress_for_tasks(task_queryset):
    """Aggregate progress for a queryset of tasks."""
    tasks = list(task_queryset.select_related("milestone", "project"))
    now = timezone.now()
    total_planned = Decimal("0")
    weighted = Decimal("0")
    completed_task_count = sum(1 for t in tasks if t.status == Task.Status.COMPLETED)

    for t in tasks:
        planned = planned_hours_for_task(t)
        if planned <= 0:
            continue
        total_planned += planned
        pct = Decimal(str(task_progress_percent(t, now)))
        weighted += planned * (pct / Decimal(100))

    task_count = len(tasks)
    progress_percent = None
    if total_planned > 0:
        progress_percent = _quantize_percent((weighted / total_planned) * Decimal(100))

    return {
        "progress_percent": progress_percent,
        "total_planned_hours": float(total_planned),
        "weighted_progress_hours": float(weighted),
        # Back-compat field names (same numeric meaning as before serializers)
        "total_estimated_hours": float(total_planned),
        "weighted_complete_hours": float(weighted),
        "task_count": task_count,
        "completed_task_count": completed_task_count,
    }


def project_progress_data(project: Project) -> dict:
    """Full project rollup plus per-milestone rows and optional unmilestoned tasks."""
    base = Task.objects.filter(project_id=project.id)
    snapshot = work_tracking_progress_for_tasks(base)

    milestone_rows = []
    for m in Milestone.objects.filter(project_id=project.id).order_by("milestone_no", "id"):
        ms = base.filter(milestone_id=m.id)
        row = {
            "milestone_id": m.id,
            "milestone_no": m.milestone_no,
            "name": m.name,
            "status": m.status,
        }
        row.update(work_tracking_progress_for_tasks(ms))
        milestone_rows.append(row)

    unmilestone_qs = base.filter(milestone__isnull=True)
    unmilestoned = None
    if unmilestone_qs.exists():
        unmilestoned = work_tracking_progress_for_tasks(unmilestone_qs)

    out = {
        "project_id": project.id,
        "project_name": project.name,
        "project_status": project.status,
    }
    out.update(snapshot)
    out["milestones"] = milestone_rows
    out["unmilestoned_tasks"] = unmilestoned
    return out


def milestone_progress_data(milestone: Milestone) -> dict:
    """Single milestone rollup (tasks belonging to this milestone only)."""
    qs = Task.objects.filter(milestone_id=milestone.id)
    out = {
        "milestone_id": milestone.id,
        "milestone_no": milestone.milestone_no,
        "name": milestone.name,
        "status": milestone.status,
        "project_id": milestone.project_id,
        "project_name": milestone.project.name,
    }
    out.update(work_tracking_progress_for_tasks(qs))
    return out
