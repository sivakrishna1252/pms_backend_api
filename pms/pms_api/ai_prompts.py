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

SELF_REF_RE = re.compile(
    r"(?:"
    r"\bwho am i\b|"
    r"\babout me\b|"
    r"\bmy\s+(?:email|role|department|profile)\b|"
    r"\bmy\s+(?:tasks?|attendance|leave)\b|"
    r"\bwhat(?:'s| is|s)\s+my\s+\w+\b"
    r")",
    re.IGNORECASE,
)

_GREETING_WORDS = frozenset(
    {"hi", "hello", "hey", "hii", "helo", "hloo", "hlee", "howdy", "namaste", "yo", "sup"}
)
_GREETING_FILLERS = frozenset({"good", "morning", "afternoon", "evening", "there"})

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


def _normalize_chat(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()


def _word_like_name(word: str) -> bool:
    import difflib

    w = _normalize_chat(word)
    if not w:
        return False
    return difflib.SequenceMatcher(None, w, "name").ratio() >= 0.65


def is_self_referential_question(question: str) -> bool:
    """True when the admin asks about themselves (my name, who am I, my tasks)."""
    text = (question or "").strip()
    if not text:
        return False
    if SELF_REF_RE.search(text):
        return True
    for match in re.finditer(r"\bmy\s+(\w+)\b", text, re.I):
        if _word_like_name(match.group(1)):
            return True
    if re.search(r"\b(show|tell|give)\s+me\b", text, re.I):
        return False
    return bool(re.search(r"\b(me|mine|myself)\b", text, re.I) and re.search(r"\babout\b", text, re.I))


def _word_is_greeting(word: str) -> bool:
    import difflib

    w = _normalize_chat(word)
    if not w:
        return False
    if w in _GREETING_WORDS or w in _GREETING_FILLERS:
        return True
    return any(difflib.SequenceMatcher(None, w, g).ratio() >= 0.72 for g in _GREETING_WORDS)


def is_greeting(question: str) -> bool:
    """Short hello/hi messages (typos tolerated) — not data questions."""
    norm = _normalize_chat(question)
    words = [w for w in norm.split() if w]
    if not words or len(words) > 4:
        return False
    return all(_word_is_greeting(w) for w in words)


def try_greeting_reply(question: str, payload: dict[str, Any]) -> str | None:
    if not is_greeting(question):
        return None
    admin = payload.get("asking_admin") or {}
    name = admin.get("full_name") or "there"
    return (
        f"Hello, {name}! I'm your PMS assistant. "
        "Ask me about tasks, projects, attendance, leave, or any employee's update."
    )


def try_self_identity_reply(question: str, payload: dict[str, Any]) -> str | None:
    if not is_self_referential_question(question):
        return None
    admin = payload.get("asking_admin")
    if not admin:
        return None

    text = (question or "").lower()
    name = admin.get("full_name") or admin.get("email") or "Unknown"
    role = admin.get("role_label") or admin.get("role") or "Admin"
    email = admin.get("email") or ""

    if re.search(r"\bemail\b", text):
        return f"You are logged in as {name}. Your email is {email}."
    if re.search(r"\brole\b", text):
        return f"You are logged in as {name}. Your role is {role}."
    if re.search(r"\btasks?\b", text):
        ctx = payload.get("question_user_context") or {}
        summary = ctx.get("assigned_task_summary") or {}
        total = summary.get("total_assigned", 0)
        delayed = summary.get("delayed", 0)
        in_prog = summary.get("in_progress", 0)
        return (
            f"You are {name}. You have {total} assigned tasks: "
            f"{in_prog} in progress, {delayed} delayed."
        )
    return f"You are logged in as {name} ({role})."


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
            "If asked about attendance or leave, say attendance data could not be loaded."
        )
    )
    return (
        "You are a friendly admin assistant for a Project Management System (PMS) and HRMS attendance module.\n"
        "The admin may ask anything in casual English — short phrases, broken grammar, or typos are normal.\n\n"
        "CORE RULES:\n"
        "1) READ-ONLY: Refuse only clear action commands (create/assign/delete). "
        "Status, update, compare, and list questions are always read-only.\n"
        "2) Use ONLY the JSON snapshot. Never invent names, numbers, or dates.\n"
        "3) If data is missing, say so plainly.\n\n"
        "WHO IS ASKING:\n"
        "4) asking_admin is the logged-in admin who sent the message. "
        "When they say 'my name', 'who am I', 'my email', 'my role', or 'me' — answer using asking_admin, "
        "NOT staff_directory. Never say you cannot see their name if asking_admin is present.\n\n"
        "GREETINGS & SMALL TALK:\n"
        "5) For hi/hello/hey (even with typos like 'hlee'): reply with ONE short friendly sentence "
        "and offer to help. Do NOT dump project counts, user counts, or system stats unless they asked.\n\n"
        "DATA SOURCES:\n"
        f"6) Attendance/leave: {attendance_note}\n"
        "7) Projects/tasks: task_status_counts, ai_briefing, projects, milestones, tasks.\n"
        "8) User counts: portal_user_counts. Staff list: staff_directory.\n\n"
        "SPELLING & OTHER PEOPLE'S NAMES:\n"
        "9) Match misspelled employee names via staff_directory (e.g. 'pratika' → Pratik Parade).\n"
        "10) question_user_context: matched_person (one person), matched_people (several named), "
        "ambiguous_people (unclear — ask which one).\n\n"
        "EMPLOYEE TASK & PERFORMANCE (critical):\n"
        "11) Use question_user_context.employee_work_brief and assigned_task_summary — "
        "assigned_tasks is the COMPLETE list from the database.\n"
        "12) NEVER invent task names, counts, projects, or deadlines. "
        "If a task is not listed, it does not exist for that person.\n"
        "13) For 'rate out of 5' or performance: only use pre-computed scores in employee_work_brief "
        "or state factual counts and hours — never guess a single task or make up 'due tomorrow' dates.\n"
        "14) 'Today' means as_of_date in the snapshot — do not say 'tomorrow' for today's date.\n"
        "15) report_period / employee_work_brief.period defines the date range "
        "(this week, last week, this month, Monday-to-Monday, etc.) — always state those dates in the answer.\n"
        "16) working_time_in_period is from time logs between period start and end only.\n"
        "17) List all tasks with status, project, and time spent when asked about someone's work.\n\n"
        "COMPARISONS:\n"
        "16) Side-by-side answers when comparing employees, tasks, or projects.\n\n"
        "RESPONSE STYLE:\n"
        "- Plain English, direct answer first, short bullets if needed.\n"
        "- Match answer length to the question — one word in, one or two sentences out.\n"
        "- No JSON field names or jargon."
    )


def build_user_message(*, context_text: str, question: str, attendance_focus: bool) -> str:
    focus_parts: list[str] = []
    if is_greeting(question):
        focus_parts.append(
            "This is a greeting only — reply briefly (1-2 sentences). Do NOT list system stats or counts."
        )
    elif is_self_referential_question(question):
        focus_parts.append(
            "The admin is asking about THEMSELVES — use asking_admin in the snapshot, not staff_directory."
        )
    else:
        focus_parts.append(
            "Treat typos as normal. Match employee names via staff_directory when needed."
        )
    if attendance_focus:
        focus_parts.append("Focus on attendance_snapshot and attendance_ai_briefing.")
    if YESTERDAY_RE.search(question or ""):
        focus_parts.append("Use yesterday attendance fields, not today.")
    if re.search(r"\bcompare\b|\bcomparison\b|\bvs\b|\bversus\b|\bbetween\b", question or "", re.I):
        focus_parts.append("Give a side-by-side comparison.")
    if re.search(
        r"\b(this week|this weak|last week|this month|last month|week report|monday|moday|weekly|monthly)\b",
        question or "",
        re.I,
    ):
        focus_parts.append(
            "Use employee_work_brief.period for the exact date range. "
            "Show working_time_in_period and which tasks were active in that period."
        )
    if re.search(r"\b(performance|performence|rating|rate|score|out of 5|/5)\b", question or "", re.I):
        focus_parts.append(
            "Performance/rating question — use employee_work_brief only. "
            "List ALL assigned_tasks. Never invent tasks or dates."
        )
    elif re.search(r"\btasks?\b", question or "", re.I) and re.search(
        r"\b(how many|total|count|list|update|status|doing|assigned)\b", question or "", re.I
    ):
        focus_parts.append(
            "Task question — use question_user_context.assigned_task_summary.assigned_tasks as the "
            "complete task list. Do not invent tasks or ratings."
        )
    focus = "\n".join(focus_parts) + "\n\n"
    return (
        f"{focus}"
        f"Data snapshot (JSON, read-only):\n{context_text}\n\n"
        f"Admin question (exact text, may contain typos):\n{question}\n\n"
        "Reply in plain English."
    )
