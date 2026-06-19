import json
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from pms_api.attendance_client import fetch_attendance_readonly_snapshot
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
        "plain_english_hints": [
            f"There are {tsc.get('delayed', 0)} delayed tasks, {tsc.get('blocked', 0)} blocked, "
            f"{tsc.get('in_progress', 0)} in progress, and {tsc.get('completed', 0)} completed.",
            f"Total active projects in snapshot: {len(projects)}.",
        ],
        "instructions": (
            "Use per_project_briefing for natural language: name who is on tasks, which milestones are IN_PROGRESS, "
            "completed vs remaining tasks, project deadline, and days_until_project_deadline. "
            "Mention unassigned work only if a task has no assignee in the task list (assigned_to_name null)."
        ),
    }


def _build_attendance_ai_briefing(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Plain-English hints so Sarvam answers attendance/leave questions clearly."""
    summary = snapshot.get("attendance_summary_today") or {}
    yesterday = snapshot.get("attendance_summary_yesterday") or {}
    leave = snapshot.get("leave_status_counts") or {}
    on_leave = snapshot.get("employees_on_approved_leave_today") or []
    pending = snapshot.get("pending_leave_requests") or []
    holidays = snapshot.get("upcoming_holidays") or []

    on_leave_names = [row.get("employee_name") for row in on_leave if row.get("employee_name")]
    pending_names = [row.get("employee_name") for row in pending[:10] if row.get("employee_name")]

    hints = [
        (
            f"Today ({snapshot.get('as_of_date')}): "
            f"{summary.get('checked_in_today', 0)} employees checked in, "
            f"{summary.get('checked_out_today', 0)} checked out, "
            f"{summary.get('still_present_not_checked_out', 0)} still present without checkout."
        ),
    ]
    if yesterday.get("date"):
        hints.append(
            (
                f"Yesterday ({yesterday.get('date')}): "
                f"{yesterday.get('checked_in', 0)} checked in, "
                f"{yesterday.get('checked_out', 0)} checked out, "
                f"{yesterday.get('still_present_not_checked_out', 0)} still present without checkout, "
                f"{yesterday.get('records', 0)} attendance records."
            )
        )
    hints.extend(
        [
            (
                f"Leave requests — pending: {leave.get('pending', 0)}, "
                f"approved (all time in DB): {leave.get('approved', 0)}, "
                f"rejected: {leave.get('rejected', 0)}."
            ),
        ]
    )
    if on_leave_names:
        hints.append(
            f"On approved leave today ({len(on_leave)}): {', '.join(on_leave_names[:15])}."
        )
    else:
        hints.append("No employees on approved leave today.")
    if pending_names:
        hints.append(f"Pending leave — waiting for approval ({len(pending)}): {', '.join(pending_names)}.")
    if holidays:
        next_h = holidays[0]
        hints.append(f"Next holiday: {next_h.get('name')} on {next_h.get('holiday_date')}.")

    return {
        "plain_english_hints": hints,
        "key_numbers": {
            "checked_in_today": summary.get("checked_in_today", 0),
            "pending_leave_requests": leave.get("pending", 0),
            "on_leave_today_count": len(on_leave),
        },
    }


def _merge_attendance_into_payload(payload: dict[str, Any]) -> None:
    attendance_snapshot = fetch_attendance_readonly_snapshot()
    if attendance_snapshot and not attendance_snapshot.get("_note"):
        payload["attendance_snapshot"] = attendance_snapshot
        payload["attendance_ai_briefing"] = _build_attendance_ai_briefing(attendance_snapshot)
        payload["attendance_data_available"] = True
    else:
        payload["attendance_snapshot"] = {
            "_note": (
                "Attendance data unavailable. Ensure attendance_service is running, "
                "ATTENDANCE_API_BASE_URL is set, or ATTENDANCE_DB_NAME points to the attendance database."
            )
        }
        payload["attendance_ai_briefing"] = {"plain_english_hints": ["Attendance data is not loaded."]}
        payload["attendance_data_available"] = False


def _compact_payload_for_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Shrink large PMS payload but always keep attendance + briefings + user context."""
    attendance_snapshot = payload.get("attendance_snapshot")
    attendance_briefing = payload.get("attendance_ai_briefing")
    attendance_available = payload.get("attendance_data_available", False)
    pbrief = _build_ai_briefing({**payload, "tasks": (payload.get("tasks") or [])[:80]})
    return {
        "filters": payload.get("filters"),
        "overview": payload.get("overview"),
        "task_status_counts": payload.get("task_status_counts"),
        "projects": (payload.get("projects") or [])[:30],
        "milestones": (payload.get("milestones") or [])[:30],
        "tasks": (payload.get("tasks") or [])[:40],
        "ai_briefing": pbrief,
        "attendance_snapshot": attendance_snapshot,
        "attendance_ai_briefing": attendance_briefing,
        "attendance_data_available": attendance_available,
        "staff_directory": payload.get("staff_directory"),
        "question_user_context": payload.get("question_user_context"),
        "name_resolution": payload.get("name_resolution"),
        "portal_user_counts": payload.get("portal_user_counts"),
        "_note": "Snapshot was large; PMS task list trimmed. Attendance and user context kept.",
    }


def build_readonly_context_payload(
    project_id=None,
    milestone_id=None,
    task_id=None,
    question: str | None = None,
) -> dict[str, Any]:
    """
    Read-only ORM-based snapshot (via existing admin overview builder) plus AI briefing. No writes.
    """
    from pms_api.ai_user_resolution import build_portal_user_counts, enrich_payload_for_question
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
    payload["portal_user_counts"] = build_portal_user_counts()
    _merge_attendance_into_payload(payload)

    if question:
        enrich_payload_for_question(question, payload)

    text = json.dumps(payload, default=str, ensure_ascii=False)
    if len(text) > _MAX_JSON_CHARS:
        compact = _compact_payload_for_model(payload)
        if question:
            enrich_payload_for_question(question, compact)
        return compact
    return payload


def build_readonly_context_text(
    project_id=None,
    milestone_id=None,
    task_id=None,
    question: str | None = None,
) -> str:
    payload = build_readonly_context_payload(
        project_id=project_id,
        milestone_id=milestone_id,
        task_id=task_id,
        question=question,
    )
    return json.dumps(payload, default=str, ensure_ascii=False)
