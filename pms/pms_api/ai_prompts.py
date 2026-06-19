"""Admin AI prompts and read-only guardrails."""

from __future__ import annotations

import re
from typing import Any

# Clear read-only questions (including natural phrasing and typos).
READ_ONLY_QUESTION_RE = re.compile(
    r"(?:"
    r"\bhow many\b|\bhow much\b|\bwho is\b|\bwho are\b|\bwho was\b|\bwho has\b|"
    r"\bwhat is\b|\bwhat are\b|\bwhat was\b|\bwhat about\b|\bwhich\b|\bwhen\b|\bwhere\b|"
    r"\blist\b|\bshow me\b|\bshow all\b|\btell me\b|\bgive me\b|\bcount\b|\bsummary\b|"
    r"\bstatus of\b|\bstatus for\b|\bis there\b|\bare there\b|\bdo we have\b|\bdid anyone\b|"
    r"\bhow is\b|\bhow are\b|\bany update\b|\blatest update\b|\bprogress of\b|\bprogress on\b|"
    r"\bcompare\b|\bcomparison\b|\bdifference\b|\bvs\b|\bversus\b|\bbetween\b|\bbetter\b|\bworse\b"
    r")",
    re.IGNORECASE,
)

# "Update" as status/progress (NOT a command to change data).
STATUS_READ_RE = re.compile(
    r"(?:"
    r"what'?s?\s+(?:the\s+)?update\b|"
    r"what is the update\b|"
    r"\bupdate of\b|\bupdate on\b|\bupdate for\b|"
    r"\bgive me (?:the\s+)?update\b|\btell me (?:the\s+)?update\b|"
    r"\bproject update\b|\btask update\b|\bcurrent update\b"
    r")",
    re.IGNORECASE,
)

# Past tense / reporting — "how many tasks created today" is read-only.
CREATED_READ_RE = re.compile(
    r"(?:"
    r"\bhow many\b.*\bcreated\b|"
    r"\btasks?\s+created\b|\bcreated\s+(?:today|yesterday|this week|this month)\b|"
    r"\bwere\s+.+\s+created\b"
    r")",
    re.IGNORECASE,
)

# Actual commands to mutate data (narrow — avoid blocking status questions).
IMPERATIVE_WRITE_RE = re.compile(
    r"(?:"
    r"^\s*(?:please\s+|can you\s+|could you\s+|kindly\s+)?(?:"
    r"create|add|delete|remove|edit|modify|assign|reassign|unassign|approve|reject"
    r")\b|"
    r"\b(?:please|can you|could you)\s+(?:create|add|delete|remove|update|edit|assign|approve|reject)\b|"
    r"\b(?:update|change|set)\s+(?:the\s+)?(?:task|project|milestone|status|deadline)(?:\s+\S+)?\s+to\b|"
    r"\bdelete\s+(?:the\s+)?(?:task|project|milestone)\b|"
    r"\bcreate\s+(?:a\s+)?(?:new\s+)?(?:task|project|milestone)\b|"
    r"\bmark\s+(?:the\s+)?(?:task|project)\s+as\b|"
    r"\bset\s+status\s+to\b|"
    r"\bcomplete\s+(?:the\s+)?task\b|"
    r"\bclose\s+(?:the\s+)?task\b"
    r")",
    re.IGNORECASE,
)

ATTENDANCE_TOPIC_RE = re.compile(
    r"\b("
    r"attendance|check[\s-]?in|check[\s-]?out|present|absent|late|"
    r"leave|holiday|wfh|work from home|sick leave|annual leave|casual leave|"
    r"yesterday|today"
    r")\b",
    re.IGNORECASE,
)

YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)

READ_ONLY_REFUSAL = (
    "I don't have access to create, update, or delete anything in the system. "
    "I'm read-only — I can only answer questions about existing data. "
    "Please use the admin pages in the app to make changes."
)


def is_write_intent(question: str) -> bool:
    """True only when the admin is clearly asking the AI to mutate data."""
    text = (question or "").strip()
    if not text:
        return False
    if READ_ONLY_QUESTION_RE.search(text):
        return False
    if STATUS_READ_RE.search(text):
        return False
    if CREATED_READ_RE.search(text):
        return False
    return bool(IMPERATIVE_WRITE_RE.search(text))


def is_attendance_question(question: str) -> bool:
    return bool(ATTENDANCE_TOPIC_RE.search(question or ""))


def try_yesterday_attendance_reply(question: str, payload: dict[str, Any]) -> str | None:
    if not YESTERDAY_RE.search(question or ""):
        return None
    if not is_attendance_question(question):
        return None

    snap = payload.get("attendance_snapshot") or {}
    summary = snap.get("attendance_summary_yesterday") or {}
    if not summary.get("date"):
        return None

    lines = [
        (
            f"Yesterday ({summary['date']}): "
            f"{summary.get('checked_in', 0)} employees checked in, "
            f"{summary.get('checked_out', 0)} checked out, and "
            f"{summary.get('still_present_not_checked_out', 0)} were still present without checkout."
        ),
        f"There were {summary.get('records', 0)} attendance records for that day.",
    ]

    logs = snap.get("attendance_logs_yesterday") or []
    if logs:
        names = sorted(
            {row.get("employee_name") for row in logs if row.get("employee_name")}
        )
        if names:
            lines.append(f"Employees with attendance records: {', '.join(names[:15])}.")

    return "\n".join(lines)


def build_system_prompt(*, attendance_available: bool) -> str:
    attendance_note = (
        "Attendance/leave data is in attendance_snapshot and attendance_ai_briefing."
        if attendance_available
        else (
            "Attendance/leave data is currently unavailable in this snapshot. "
            "If asked about attendance or leave, say attendance data could not be loaded "
            "and suggest checking that the attendance service or ATTENDANCE_DB is reachable."
        )
    )
    return (
          "You are a friendly admin assistant for a Project Management System and HRMS attendance module.\n"
        "RULES:\n"
        "1) READ-ONLY: You cannot create, update, delete, approve, reject, or assign anything in the database. "
        "If the admin asks you to perform an action, say: I don't have access to do that. "
        "If they ask for a status/update/progress report (e.g. what's the update on a project), answer from the data — "
        "that is NOT a write request.\n"
        "2) Use ONLY the JSON snapshot provided. Never invent names, numbers, or dates.\n"
        "3) If data is missing, say clearly that it is not in the snapshot.\n"
        "4) PMS answers: use task_status_counts, ai_briefing, projects, milestones, tasks.\n"
        f"5) {attendance_note}\n"
        "   For yesterday attendance, use attendance_summary_yesterday and attendance_logs_yesterday.\n"
        "   For today attendance, use attendance_summary_today.\n"
        "6) USER ROLE COUNTS: use portal_user_counts (matches User Management page — excludes Django staff/superuser accounts).\n"
        "7) For delayed tasks, use task_status_counts.delayed and tasks with status DELAYED.\n"
        "8) PEOPLE / NAMES:\n"
        "   - Admins may misspell names (e.g. Pratik Parada vs Pratik Paradi). Use staff_directory and "
        "question_user_context to match the intended person.\n"
        "   - If question_user_context.matched_person exists, answer about THAT person using assigned_task_summary.\n"
        "   - If ambiguous_people lists multiple matches, ask which person they mean and show each name with role.\n"
        "RESPONSE STYLE (very important):\n"
        "- Write in simple, natural English that a non-technical admin understands.\n"
        "- Start with a direct one-sentence answer.\n"
        "- Then add short bullet points only when they help.\n"
        "- Use employee names and dates from the data.\n"
        "- Avoid JSON field names, code, or technical jargon.\n"
        "- Keep answers concise (under 150 words unless listing many items)."
        "SPELLING & NAMES (very important):\n"
        "8) Admins often misspell names or use first names only (e.g. 'pratika' → Pratik Parade, "
        "'siva' → Siva Krishna, 'parada' → Parade). Match the closest person in staff_directory "
        "using fuzzy / phonetic similarity — do NOT reject the question because of a typo.\n"
        "9) question_user_context helps pre-match people:\n"
        "   - matched_person → answer about that one person using assigned_task_summary.\n"
        "   - matched_people → admin asked about several people; answer EACH separately "
        "(tasks, attendance, leave as relevant).\n"
        "   - ambiguous_people → two or more staff share a similar name for ONE mention; "
        "briefly list the options with role/email and ask which one they mean. Do not guess.\n"
        "10) If no pre-match exists, search staff_directory and tasks yourself using the misspelled tokens.\n\n"
        "COMPARISONS & MULTI-TOPIC QUESTIONS:\n"
        "11) Compare employees, tasks, or projects side by side when asked "
        "(e.g. 'compare pratika and siva', 'who has more delayed tasks'). "
        "Use a short summary per item, then a one-line conclusion if helpful.\n"
        "12) Mixed questions (tasks + attendance + leave in one message) — address each part.\n"
        "13) Time phrases: 'this week', 'today', 'yesterday' — filter or describe using dates in the snapshot.\n\n"
        "WHEN TO ASK A CLARIFYING QUESTION:\n"
        "14) Ask 'which one?' ONLY when two or more real employees match one unclear name and you "
        "cannot tell from context. Never ask when the admin already named multiple distinct people.\n"
        "15) Do not ask clarifying questions for typos you can resolve confidently from staff_directory.\n\n"
        "RESPONSE STYLE:\n"
        "- Plain English for a non-technical admin.\n"
        "- Start with a direct answer, then bullets if useful.\n"
        "- Use real employee names, project names, and dates from the data.\n"
        "- No JSON field names, code, or jargon.\n"
        "- Stay concise unless comparing several people or listing many items."
    )


def build_user_message(*, context_text: str, question: str, attendance_focus: bool) -> str:
    focus_parts: list[str] = [
        "Treat the admin question as natural language — fix typos mentally and match names to staff_directory.",
        "Answer from the snapshot; only ask which person they mean if ambiguous_people applies and context is unclear.",
    ]
    if attendance_focus:
        focus_parts.append("Focus on attendance_snapshot and attendance_ai_briefing for this question.")
    if YESTERDAY_RE.search(question or ""):
        focus_parts.append(
            "The admin asked about YESTERDAY — use attendance_summary_yesterday and attendance_logs_yesterday, "
            "not today's summary."
        )
    if re.search(r"\bcompare\b|\bcomparison\b|\bvs\b|\bversus\b|\bbetween\b|\bdifference\b", question or "", re.I):
        focus_parts.append(
            "This is a comparison — give a side-by-side answer for each person, task, or project mentioned."
        )
    focus = "\n".join(focus_parts) + "\n\n"
    return (
        f"{focus}"
        f"Data snapshot (JSON, read-only):\n{context_text}\n\n"
        f"Admin question (exact text, may contain typos):\n{question}\n\n"
        "Reply in plain English."
    )
