import json
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from pms_api.models import Milestone, Task

# Keep prompts bounded for local model context; admin can narrow with optional filters.
_MAX_JSON_CHARS = 100_000
_MAX_TASKS = 150


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")[:10]).date()
        except ValueError:
            return None
    return None


def _build_ai_briefing(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Pre-computed hints so the model can answer in a short status-briefing style
    (who is on the work, milestones in progress, task completion, deadlines, days left).
    """
    today = timezone.now().date()
    projects = payload.get("projects") or []
    milestones = payload.get("milestones") or []
    tasks = payload.get("tasks") or []
    tsc = payload.get("task_status_counts") or {}

    per_project: list[dict[str, Any]] = []
    for proj in projects:
        pid = proj.get("id")
        p_tasks = [t for t in tasks if t.get("project_id") == pid]
        p_milestones = [m for m in milestones if m.get("project_id") == pid]

        assignees = sorted(
            {t.get("assigned_to_name") for t in p_tasks if t.get("assigned_to_name")}
        )
        completed = sum(1 for t in p_tasks if t.get("status") == Task.Status.COMPLETED)
        total = len(p_tasks)
        remaining = total - completed

        pdead = _as_date(proj.get("deadline"))
        days_to_project_deadline = (pdead - today).days if pdead else None

        ms_brief = []
        for m in sorted(p_milestones, key=lambda x: (x.get("milestone_no") or 0)):
            end = _as_date(m.get("end_date"))
            days_to_ms = (end - today).days if end else None
            ms_brief.append(
                {
                    "milestone_no": m.get("milestone_no"),
                    "name": m.get("name"),
                    "status": m.get("status"),
                    "start_date": m.get("start_date"),
                    "end_date": m.get("end_date"),
                    "days_until_milestone_end": days_to_ms,
                }
            )
        in_progress_milestones = [m.get("name") for m in p_milestones if m.get("status") == Milestone.Status.IN_PROGRESS]

        in_prog_tasks = [t.get("title") for t in p_tasks if t.get("status") == Task.Status.IN_PROGRESS][:15]

        per_project.append(
            {
                "project_name": proj.get("name"),
                "project_id": pid,
                "project_status": proj.get("status"),
                "start_date": proj.get("start_date"),
                "project_deadline": proj.get("deadline"),
                "days_until_project_deadline": days_to_project_deadline,
                "is_project_past_deadline": bool(pdead and pdead < today) if pdead else None,
                "milestones_in_progress_by_name": in_progress_milestones,
                "milestone_overview": ms_brief,
                "people_assigned_to_tasks": assignees,
                "tasks": {
                    "total": total,
                    "completed": completed,
                    "remaining_not_done": remaining,
                },
                "task_titles_in_progress": in_prog_tasks,
            }
        )

    return {
        "as_of_date": today.isoformat(),
        "scope_task_status_counts": tsc,
        "per_project_briefing": per_project,
        "instructions": (
            "Use per_project_briefing for natural language: name who is on tasks, which milestones are IN_PROGRESS, "
            "completed vs remaining tasks, project deadline, and days_until_project_deadline. "
            "Mention unassigned work only if a task has no assignee in the task list (assigned_to_name null)."
        ),
    }


def build_readonly_context_text(
    project_id=None,
    milestone_id=None,
    task_id=None,
) -> str:
    """
    Read-only ORM-based snapshot (via existing admin overview builder) plus AI briefing. No writes.
    """
    from pms_api.views import build_admin_overview_payload

    payload: dict[str, Any] = build_admin_overview_payload(
        project_id=project_id,
        milestone_id=milestone_id,
        task_id=task_id,
    )
    tasks = payload.get("tasks") or []
    if len(tasks) > _MAX_TASKS:
        payload = {
            **payload,
            "tasks": tasks[:_MAX_TASKS],
            "_note": f"Task list truncated to {_MAX_TASKS} rows for the AI context.",
        }
    payload["ai_briefing"] = _build_ai_briefing(payload)
    text = json.dumps(payload, default=str, ensure_ascii=False)
    if len(text) > _MAX_JSON_CHARS:
        # Keep briefing + slimmer task list
        pbrief = _build_ai_briefing({**payload, "tasks": (payload.get("tasks") or [])[:80]})
        payload = {
            "filters": payload.get("filters"),
            "overview": payload.get("overview"),
            "task_status_counts": payload.get("task_status_counts"),
            "projects": (payload.get("projects") or [])[:30],
            "milestones": (payload.get("milestones") or [])[:30],
            "tasks": (payload.get("tasks") or [])[:40],
            "ai_briefing": pbrief,
            "_note": "Snapshot was large; optional project_id on the API request narrows context to one project.",
        }
        text = json.dumps(payload, default=str, ensure_ascii=False)
    return text
