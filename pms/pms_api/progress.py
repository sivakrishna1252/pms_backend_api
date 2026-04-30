"""Effort-weighted progress: Σ(estimated_hours × completion) / Σ(estimated_hours).

Task completion is binary: COMPLETED → full weight; any other status → 0.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When

from .models import Milestone, Project, Task

_WEIGHT_OUTPUT = DecimalField(max_digits=14, decimal_places=4)


def _coalesce_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return value


def effort_progress_for_tasks(task_queryset):
    """Return progress stats for a set of tasks (same project or same milestone).

    progress_percent is None when total estimated hours is 0 (cannot divide).
    """
    agg = task_queryset.aggregate(
        total_estimated_hours=Sum("estimated_hours"),
        weighted_complete_hours=Sum(
            Case(
                When(status=Task.Status.COMPLETED, then=F("estimated_hours")),
                default=Value(Decimal("0")),
                output_field=_WEIGHT_OUTPUT,
            )
        ),
        task_count=Count("id"),
        completed_task_count=Count("id", filter=Q(status=Task.Status.COMPLETED)),
    )
    total = _coalesce_decimal(agg["total_estimated_hours"])
    weighted = _coalesce_decimal(agg["weighted_complete_hours"])
    progress_percent = None
    if total > 0:
        progress_percent = float((weighted / total * Decimal("100")).quantize(Decimal("0.01")))
    return {
        "progress_percent": progress_percent,
        "total_estimated_hours": float(total),
        "weighted_complete_hours": float(weighted),
        "task_count": agg["task_count"] or 0,
        "completed_task_count": agg["completed_task_count"] or 0,
    }


def project_progress_data(project: Project) -> dict:
    """Full project rollup plus per-milestone rows and optional unmilestoned tasks."""
    base = Task.objects.filter(project_id=project.id)
    snapshot = effort_progress_for_tasks(base)

    milestone_rows = []
    for m in Milestone.objects.filter(project_id=project.id).order_by("milestone_no", "id"):
        ms = base.filter(milestone_id=m.id)
        row = {
            "milestone_id": m.id,
            "milestone_no": m.milestone_no,
            "name": m.name,
            "status": m.status,
        }
        row.update(effort_progress_for_tasks(ms))
        milestone_rows.append(row)

    unmilestone_qs = base.filter(milestone__isnull=True)
    unmilestoned = None
    if unmilestone_qs.exists():
        unmilestoned = effort_progress_for_tasks(unmilestone_qs)

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
    out.update(effort_progress_for_tasks(qs))
    return out
