"""Factual employee task / performance answers computed from the database (no LLM guessing)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from pms_api.models import Task, TimeLog, humanize_duration

from .ai_user_resolution import fetch_assigned_tasks_for_user, find_people_in_question, load_staff_directory

PERFORMANCE_RE = re.compile(
    r"(?:"
    r"\bperformance\b|\bperformence\b|\bperformanc\b|"
    r"\brating\b|\brate\b|\bscore\b|\bout of 5\b|\b/5\b|\bstars?\b"
    r")",
    re.IGNORECASE,
)

TASK_COUNT_RE = re.compile(
    r"\b(?:how many|total|totaly|totally|count|number of)\b.*\btasks?\b|\btasks?\b.*\b(?:how many|total|count)\b",
    re.IGNORECASE,
)

EMPLOYEE_REPORT_RE = re.compile(
    r"(?:"
    r"\bupdate\b|\breport\b|\bsummary\b|\bprogress\b|\bworking hours\b|"
    r"\bthis week\b|\bthis weak\b|\blast week\b|\bthis month\b|\blast month\b|"
    r"\bweek(?:ly)?\b|\bmonth(?:ly)?\b|\bweak\b"
    r")",
    re.IGNORECASE,
)

_WEEKDAY_ALIASES: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "moday": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_STATUS_LABEL = {
    Task.Status.NOT_STARTED: "Not started",
    Task.Status.IN_PROGRESS: "In progress",
    Task.Status.PAUSED: "Paused",
    Task.Status.COMPLETED: "Completed",
    Task.Status.DELAYED: "Delayed",
    Task.Status.BLOCKED: "Blocked",
}


@dataclass(frozen=True)
class ReportPeriod:
    kind: str
    label: str
    start: date
    end: date

    @property
    def date_range_text(self) -> str:
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()} to {self.end.isoformat()}"


def _normalize_q(question: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (question or "").lower()).strip()


def parse_report_period(question: str, today: date | None = None) -> ReportPeriod:
    """
    Resolve the date range the admin means (typos tolerated).

    Examples:
    - "this week" / "this weak" → Monday of current week through today
    - "last week" → previous Mon–Sun
    - "this month" → 1st of month through today
    - "last month" → full previous calendar month
    - "monday" / "moday" → previous Monday through this Monday
    - "week report" → Monday through yesterday (if not Monday today)
    - "1 week" / "7 days" → rolling 7 days ending yesterday
    """
    today = today or timezone.now().date()
    q = _normalize_q(question)

    if re.search(r"\b(?:last|previous)\s+month\b", q):
        first_this = today.replace(day=1)
        end = first_this - timedelta(days=1)
        start = end.replace(day=1)
        return ReportPeriod(
            "last_month",
            f"Last month ({start.isoformat()} to {end.isoformat()})",
            start,
            end,
        )

    if re.search(r"\b(?:this|current)\s+month\b", q) or re.search(r"\bmonthly\b", q):
        start = today.replace(day=1)
        return ReportPeriod(
            "this_month",
            f"This month ({start.isoformat()} to {today.isoformat()})",
            start,
            today,
        )

    if re.search(r"\b(?:last|previous)\s+week\b", q):
        this_mon = today - timedelta(days=today.weekday())
        start = this_mon - timedelta(days=7)
        end = start + timedelta(days=6)
        return ReportPeriod(
            "last_week",
            f"Last week ({start.isoformat()} to {end.isoformat()})",
            start,
            end,
        )

    if re.search(r"\b(?:1 week|one week|7 days|seven days)\b", q):
        end = today - timedelta(days=1)
        start = end - timedelta(days=6)
        return ReportPeriod(
            "rolling_7",
            f"Last 7 days ({start.isoformat()} to {end.isoformat()})",
            start,
            end,
        )

    for name, weekday in _WEEKDAY_ALIASES.items():
        if re.search(rf"\b{re.escape(name)}\b", q):
            days_since = (today.weekday() - weekday) % 7
            this_occurrence = today - timedelta(days=days_since)
            prev_occurrence = this_occurrence - timedelta(days=7)
            return ReportPeriod(
                "weekday_window",
                f"Previous {name.title()} to this {name.title()} ({prev_occurrence.isoformat()} to {this_occurrence.isoformat()})",
                prev_occurrence,
                this_occurrence,
            )

    is_week = bool(
        re.search(
            r"\b(?:this week|this weak|current week|the week|weekly|week report|week update|week performance)\b",
            q,
        )
        or (re.search(r"\bweak\b", q) and not re.search(r"\bweekday\b", q))
    )
    if is_week:
        start = today - timedelta(days=today.weekday())
        if re.search(r"\bweek report\b", q) and today.weekday() > 0:
            end = today - timedelta(days=1)
        else:
            end = today
        return ReportPeriod(
            "this_week",
            f"This week ({start.isoformat()} to {end.isoformat()})",
            start,
            end,
        )

    if re.search(r"\byesterday\b", q):
        y = today - timedelta(days=1)
        return ReportPeriod("yesterday", f"Yesterday ({y.isoformat()})", y, y)

    if re.search(r"\btoday\b", q):
        return ReportPeriod("today", f"Today ({today.isoformat()})", today, today)

    if re.search(r"\b(?:week|weak)\b", q):
        start = today - timedelta(days=today.weekday())
        return ReportPeriod(
            "this_week",
            f"This week ({start.isoformat()} to {today.isoformat()})",
            start,
            today,
        )

    if re.search(r"\bmonth\b", q):
        start = today.replace(day=1)
        return ReportPeriod(
            "this_month",
            f"This month ({start.isoformat()} to {today.isoformat()})",
            start,
            today,
        )

    return ReportPeriod("all_time", "All time (all assigned tasks)", today, today)


def is_performance_or_rating_question(question: str) -> bool:
    return bool(PERFORMANCE_RE.search(question or ""))


def is_task_count_question(question: str) -> bool:
    text = question or ""
    return bool(TASK_COUNT_RE.search(text)) and not is_performance_or_rating_question(text)


def is_employee_period_report_question(question: str) -> bool:
    """Update/report with a time window, about a specific employee."""
    text = question or ""
    if is_performance_or_rating_question(text) or is_task_count_question(text):
        return False
    if not EMPLOYEE_REPORT_RE.search(text):
        return False
    return bool(find_people_in_question(text, load_staff_directory()))


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return None


def _working_seconds_for_user(user_id: int, start: date, end: date) -> int:
    total = (
        TimeLog.objects.filter(
            user_id=user_id,
            start_time__date__gte=start,
            start_time__date__lte=end,
        ).aggregate(s=Sum("duration_seconds"))
    ).get("s") or 0
    return int(total)


def _task_seconds_in_period(user_id: int, task_id: int, start: date, end: date) -> int:
    total = (
        TimeLog.objects.filter(
            user_id=user_id,
            task_id=task_id,
            start_time__date__gte=start,
            start_time__date__lte=end,
        ).aggregate(s=Sum("duration_seconds"))
    ).get("s") or 0
    return int(total)


def build_employee_work_brief(user_id: int, period: ReportPeriod | None = None) -> dict[str, Any]:
    tasks = fetch_assigned_tasks_for_user(user_id)
    today = timezone.now().date()
    period = period or ReportPeriod("all_time", "All time", today, today)

    if period.kind == "all_time":
        period_seconds = sum(int(t.get("total_time_spent_seconds") or 0) for t in tasks)
    else:
        period_seconds = _working_seconds_for_user(user_id, period.start, period.end)

    all_seconds = sum(int(t.get("total_time_spent_seconds") or 0) for t in tasks)

    task_rows = []
    active_in_period = 0
    for t in tasks:
        deadline = _as_date(t.get("deadline"))
        tid = t.get("id")
        logged_in_period = 0
        if period.kind != "all_time" and tid:
            logged_in_period = _task_seconds_in_period(user_id, int(tid), period.start, period.end)

        deadline_in_period = bool(
            deadline and period.kind != "all_time" and period.start <= deadline <= period.end
        )
        worked_in_period = logged_in_period > 0
        is_active = worked_in_period or deadline_in_period

        if is_active:
            active_in_period += 1

        row = {
            "title": t.get("title"),
            "status": t.get("status"),
            "status_label": _STATUS_LABEL.get(t.get("status"), t.get("status")),
            "project_name": t.get("project_name") or "—",
            "deadline": deadline.isoformat() if deadline else None,
            "time_spent_total": t.get("total_time_spent_display") or "0 sec",
            "time_spent_in_period": humanize_duration(logged_in_period) if period.kind != "all_time" else None,
            "active_in_period": is_active,
            "deadline_in_period": deadline_in_period,
        }
        task_rows.append(row)

    counts = {
        "total_assigned": len(tasks),
        "completed": sum(1 for t in tasks if t.get("status") == Task.Status.COMPLETED),
        "in_progress": sum(1 for t in tasks if t.get("status") == Task.Status.IN_PROGRESS),
        "paused": sum(1 for t in tasks if t.get("status") == Task.Status.PAUSED),
        "not_started": sum(1 for t in tasks if t.get("status") == Task.Status.NOT_STARTED),
        "delayed": sum(1 for t in tasks if t.get("status") == Task.Status.DELAYED),
        "blocked": sum(1 for t in tasks if t.get("status") == Task.Status.BLOCKED),
        "active_in_period": active_in_period,
    }

    return {
        "as_of_date": today.isoformat(),
        "period": {
            "kind": period.kind,
            "label": period.label,
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
            "date_range": period.date_range_text,
        },
        **counts,
        "working_time_in_period": humanize_duration(period_seconds),
        "working_seconds_in_period": period_seconds,
        "working_time_all_tasks": humanize_duration(all_seconds),
        "assigned_tasks": task_rows,
    }


def compute_rating_out_of_5(brief: dict[str, Any]) -> dict[str, Any]:
    """Transparent data-driven score — not a subjective HR review."""
    total = brief.get("total_assigned") or 0
    if total == 0:
        return {
            "score": None,
            "factors": ["No assigned tasks in the system — cannot compute a rating."],
        }

    completed = brief.get("completed") or 0
    in_progress = brief.get("in_progress") or 0
    delayed = brief.get("delayed") or 0
    blocked = brief.get("blocked") or 0
    paused = brief.get("paused") or 0
    period_hours = (brief.get("working_seconds_in_period") or 0) / 3600
    period_label = (brief.get("period") or {}).get("label") or "period"

    score = 2.0
    factors: list[str] = []

    completion_pts = (completed / total) * 2.5
    score += completion_pts
    factors.append(f"Completion: {completed}/{total} tasks done (+{completion_pts:.1f})")

    if in_progress:
        active_pts = min(0.5, (in_progress / total) * 0.6)
        score += active_pts
        factors.append(f"In progress: {in_progress} active (+{active_pts:.1f})")

    if delayed:
        penalty = min(1.5, (delayed / total) * 1.5)
        score -= penalty
        factors.append(f"Delayed: {delayed} tasks (-{penalty:.1f})")

    if blocked:
        penalty = min(1.0, (blocked / total) * 1.0)
        score -= penalty
        factors.append(f"Blocked: {blocked} tasks (-{penalty:.1f})")

    if paused:
        penalty = min(0.4, (paused / total) * 0.4)
        score -= penalty
        factors.append(f"Paused: {paused} tasks (-{penalty:.1f})")

    if period_hours >= 20:
        score += 0.3
        factors.append(f"Hours in {period_label}: {period_hours:.1f}h (+0.3)")
    elif period_hours >= 8:
        score += 0.15
        factors.append(f"Hours in {period_label}: {period_hours:.1f}h (+0.15)")
    elif period_hours < 2 and total > 0:
        score -= 0.2
        factors.append(f"Hours in {period_label}: {period_hours:.1f}h (-0.2)")

    score = max(1.0, min(5.0, round(score, 1)))
    return {"score": score, "factors": factors}


def _resolve_person_from_payload(question: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    ctx = payload.get("question_user_context") or {}
    person = ctx.get("matched_person")
    if person:
        return person
    matches = find_people_in_question(question, load_staff_directory())
    if len(matches) == 1:
        return matches[0]
    return None


def _period_from_question(question: str, payload: dict[str, Any]) -> ReportPeriod:
    ctx = payload.get("question_user_context") or {}
    if ctx.get("report_period"):
        p = ctx["report_period"]
        return ReportPeriod(
            p.get("kind", "this_week"),
            p.get("label", "This week"),
            date.fromisoformat(p["start"]),
            date.fromisoformat(p["end"]),
        )
    return parse_report_period(question)


def format_task_count_reply(person_name: str, brief: dict[str, Any]) -> str:
    period = brief.get("period") or {}
    lines = [
        f"{person_name} — task summary",
        f"Period: {period.get('label', 'All time')}",
        "",
        f"Total assigned tasks: {brief['total_assigned']}",
        f"- Completed: {brief['completed']}",
        f"- In progress: {brief['in_progress']}",
        f"- Paused: {brief['paused']}",
        f"- Not started: {brief['not_started']}",
        f"- Delayed: {brief['delayed']}",
        f"- Blocked: {brief['blocked']}",
    ]
    if period.get("kind") != "all_time":
        lines.extend(
            [
                "",
                f"Working time in period: {brief.get('working_time_in_period', '0 sec')}",
                f"Tasks with activity in period: {brief.get('active_in_period', 0)}",
            ]
        )
    if brief.get("assigned_tasks"):
        lines.extend(["", "All assigned tasks:"])
        for idx, t in enumerate(brief["assigned_tasks"], start=1):
            extra = ""
            if period.get("kind") != "all_time" and t.get("active_in_period"):
                extra = f" — {t.get('time_spent_in_period', '0 sec')} logged in period"
            lines.append(
                f"{idx}. {t['title']} — {t['status_label']} — {t['project_name']} — {t['time_spent_total']}{extra}"
            )
    return "\n".join(lines)


def format_period_report_reply(person_name: str, brief: dict[str, Any], *, asked_rating: bool = False) -> str:
    period = brief.get("period") or {}
    lines = [
        f"{person_name} — work report",
        f"Period: {period.get('label', 'All time')}",
        f"Date range: {period.get('date_range', '')}",
        f"As of: {brief.get('as_of_date')}",
        "",
        f"Total assigned tasks: {brief['total_assigned']}",
        f"- Completed: {brief['completed']}",
        f"- In progress: {brief['in_progress']}",
        f"- Paused: {brief['paused']}",
        f"- Not started: {brief['not_started']}",
        f"- Delayed: {brief['delayed']}",
        f"- Blocked: {brief['blocked']}",
    ]

    if period.get("kind") != "all_time":
        lines.extend(
            [
                "",
                f"Working time in this period (time logs): {brief.get('working_time_in_period', '0 sec')}",
                f"Tasks with work logged or deadline in period: {brief.get('active_in_period', 0)}",
            ]
        )

    lines.extend(["", f"Total time on all assigned tasks (lifetime): {brief.get('working_time_all_tasks', '0 sec')}"])

    if brief.get("assigned_tasks"):
        lines.extend(["", "All assigned tasks (from database):"])
        for idx, t in enumerate(brief["assigned_tasks"], start=1):
            deadline = f", deadline {t['deadline']}" if t.get("deadline") else ""
            period_note = ""
            if period.get("kind") != "all_time":
                if t.get("active_in_period"):
                    period_note = f" — active in period ({t.get('time_spent_in_period', '0 sec')} logged)"
                else:
                    period_note = " — no activity in this period"
            lines.append(
                f"{idx}. {t['title']} — {t['status_label']} — {t['project_name']} — {t['time_spent_total']}{deadline}{period_note}"
            )

    if asked_rating:
        rating = compute_rating_out_of_5(brief)
        lines.extend(["", "Performance rating (computed from task data):"])
        if rating["score"] is None:
            lines.append(rating["factors"][0])
        else:
            lines.append(f"Score: {rating['score']} / 5")
            lines.append("Based on:")
            for factor in rating["factors"]:
                lines.append(f"- {factor}")

    return "\n".join(lines)


def try_employee_task_count_reply(question: str, payload: dict[str, Any]) -> str | None:
    if not is_task_count_question(question):
        return None
    person = _resolve_person_from_payload(question, payload)
    if not person:
        return None
    period = _period_from_question(question, payload)
    brief = build_employee_work_brief(person["id"], period=period)
    name = person.get("full_name") or "Employee"
    return format_task_count_reply(name, brief)


def try_employee_performance_reply(question: str, payload: dict[str, Any]) -> str | None:
    if not is_performance_or_rating_question(question):
        return None
    person = _resolve_person_from_payload(question, payload)
    if not person:
        return None
    period = _period_from_question(question, payload)
    if period.kind == "all_time" and re.search(r"\b(?:week|weak|month)\b", _normalize_q(question)):
        period = parse_report_period(question)
    brief = build_employee_work_brief(person["id"], period=period)
    name = person.get("full_name") or "Employee"
    asked_rating = bool(re.search(r"\b(out of 5|/5|rate|rating|score)\b", question or "", re.I))
    return format_period_report_reply(name, brief, asked_rating=asked_rating)


def try_employee_period_report_reply(question: str, payload: dict[str, Any]) -> str | None:
    """Week/month/weekday update reports for a named employee."""
    if not is_employee_period_report_question(question):
        return None
    person = _resolve_person_from_payload(question, payload)
    if not person:
        return None
    period = parse_report_period(question)
    brief = build_employee_work_brief(person["id"], period=period)
    name = person.get("full_name") or "Employee"
    return format_period_report_reply(name, brief, asked_rating=False)


def attach_report_period_to_context(question: str, payload: dict[str, Any]) -> None:
    """Store parsed period on question_user_context for LLM fallback path."""
    ctx = payload.get("question_user_context")
    if not ctx:
        return
    period = parse_report_period(question)
    if period.kind == "all_time":
        return
    ctx["report_period"] = {
        "kind": period.kind,
        "label": period.label,
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "date_range": period.date_range_text,
    }
