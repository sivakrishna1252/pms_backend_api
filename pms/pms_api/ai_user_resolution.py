"""Match people mentioned in admin questions (typos tolerated) and enrich AI context."""

from __future__ import annotations

import difflib
import re
from typing import Any

from django.contrib.auth import get_user_model

from pms_api.models import Task, UserProfile

_STOPWORDS = frozenset(
    """
    a an the and or but for to of in on at by with from as is are was were be been being
    how many much who what which when where why do does did can could will would should
    tell show give list count summary status about me my all any some this that these those
    task tasks project projects milestone milestones delayed delay blocked block complete
    completed progress pending approved rejected leave attendance check checkout holiday
    employee employees user users admin ba member members today tomorrow yesterday week
    month year please help need want know get has have had having there their they them
    """.split()
)

_ROLE_LABELS = {
    UserProfile.Roles.ADMIN: "Admin",
    UserProfile.Roles.BA: "Business Analyst",
    UserProfile.Roles.EMPLOYEE: "Employee",
}

_ROLE_COUNT_RE = re.compile(
    r"(?:"
    r"\bhow many\b.*\b(?:users?|people|staff|members?|accounts?)\b.*\b(?:admin|employee|ba|business analyst)s?\b|"
    r"\bhow many\b.*\b(?:admin|employee|ba)s?\b.*\b(?:users?|people|staff|members?|accounts?)\b|"
    r"\b(?:admin|employee|ba|business analyst)s?\s+(?:role|users?|accounts?)\b|"
    r"\b(?:users?|people|staff|members?|accounts?)\s+(?:in|with)\s+(?:the\s+)?(?:admin|employee|ba)\s+role\b|"
    r"\bcount\b.*\b(?:admin|employee|ba)s?\b|"
    r"\btell me\b.*\bhow many\b.*\badmin\b"
    r")",
    re.IGNORECASE,
)

_ROLE_NAME_TOKENS = frozenset({"admin", "employee", "ba", "analyst"})


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()


def _role_label(role: str) -> str:
    return _ROLE_LABELS.get(role, (role or "User").replace("_", " ").title())


def _display_name(first: str, last: str, email: str) -> str:
    full = f"{(first or '').strip()} {(last or '').strip()}".strip()
    return full or (email or "Unknown")


def portal_users_queryset():
    """Same visibility rules as User Management in the portal (hide Django staff/superuser)."""
    User = get_user_model()
    return (
        User.objects.filter(
            is_superuser=False,
            is_staff=False,
            profile__status=UserProfile.Status.ACTIVE,
        )
        .select_related("profile")
        .order_by("first_name", "last_name", "id")
    )


def is_role_count_question(question: str) -> bool:
    return bool(_ROLE_COUNT_RE.search((question or "").strip()))


def build_portal_user_counts() -> dict[str, Any]:
    users = list(portal_users_queryset())
    admins = [u for u in users if u.profile.role == UserProfile.Roles.ADMIN]
    bas = [u for u in users if u.profile.role == UserProfile.Roles.BA]
    employees = [u for u in users if u.profile.role == UserProfile.Roles.EMPLOYEE]

    def _row(user) -> dict[str, Any]:
        return {
            "id": user.id,
            "full_name": _display_name(user.first_name, user.last_name, user.email),
            "email": user.email or "",
            "role": _role_label(user.profile.role),
        }

    return {
        "admin_count": len(admins),
        "ba_count": len(bas),
        "employee_count": len(employees),
        "total_portal_users": len(users),
        "admin_users": [_row(u) for u in admins],
        "ba_users": [_row(u) for u in bas[:20]],
        "employee_users_sample": [_row(u) for u in employees[:20]],
        "note": "Counts match User Management in the portal (active users, excluding Django staff/superuser accounts).",
    }


def try_role_count_reply(question: str, payload: dict[str, Any]) -> str | None:
    if not is_role_count_question(question):
        return None

    counts = payload.get("portal_user_counts") or {}
    text = (question or "").lower()

    if re.search(r"\badmin", text):
        names = [u.get("full_name") for u in counts.get("admin_users") or [] if u.get("full_name")]
        count = counts.get("admin_count", len(names))
        lines = [f"There are {count} admin users in the portal."]
        if names:
            lines.append(f"They are: {', '.join(names)}.")
        return "\n".join(lines)

    if re.search(r"\b(?:employee|employees)\b", text):
        count = counts.get("employee_count", 0)
        return f"There are {count} employee users in the portal."

    if re.search(r"\b(?:ba|business analyst)\b", text):
        count = counts.get("ba_count", 0)
        return f"There are {count} business analyst users in the portal."

    total = counts.get("total_portal_users") or payload.get("overview", {}).get("users_count")
    if total is not None and re.search(r"\b(?:users?|people|staff|members?)\b", text):
        return (
            f"There are {total} active portal users in total: "
            f"{counts.get('admin_count', 0)} admin, "
            f"{counts.get('ba_count', 0)} business analyst, and "
            f"{counts.get('employee_count', 0)} employee."
        )
    return None


def load_staff_directory() -> list[dict[str, Any]]:
    rows = portal_users_queryset()
    directory: list[dict[str, Any]] = []
    for user in rows:
        profile = user.profile
        directory.append(
            {
                "id": user.id,
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "full_name": _display_name(user.first_name, user.last_name, user.email),
                "email": user.email or "",
                "role": profile.role,
                "role_label": _role_label(profile.role),
                "department": profile.department or "",
            }
        )
    return directory


def _name_similarity(person: dict[str, Any], phrase: str) -> float:
    phrase_norm = _normalize(phrase)
    if not phrase_norm or len(phrase_norm) < 2:
        return 0.0

    full_norm = _normalize(person.get("full_name") or "")
    first_norm = _normalize(person.get("first_name") or "")
    last_norm = _normalize(person.get("last_name") or "")

    # Compact staff_directory entries may only include full_name (no first/last split).
    if full_norm and not first_norm and not last_norm:
        parts = full_norm.split()
        if parts:
            first_norm = parts[0]
        if len(parts) > 1:
            last_norm = parts[-1]

    scores: list[float] = []
    if full_norm and full_norm in phrase_norm:
        scores.append(1.0)
    if first_norm and len(first_norm) >= 3 and re.search(rf"\b{re.escape(first_norm)}\b", phrase_norm):
        scores.append(0.88)
    if first_norm or last_norm or full_norm:
        if full_norm:
            scores.append(difflib.SequenceMatcher(None, full_norm, phrase_norm).ratio())
        if first_norm:
            scores.append(difflib.SequenceMatcher(None, first_norm, phrase_norm).ratio())
        if last_norm:
            scores.append(difflib.SequenceMatcher(None, last_norm, phrase_norm).ratio())
        if first_norm and last_norm:
            scores.append(difflib.SequenceMatcher(None, f"{first_norm} {last_norm}", phrase_norm).ratio())

    return max(scores) if scores else 0.0


def _question_phrases(question: str) -> list[str]:
    norm = _normalize(question)
    words = [w for w in norm.split() if w and w not in _STOPWORDS and len(w) >= 2]
    phrases = [norm]
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            phrases.append(" ".join(words[i : i + n]))
    return phrases


def find_people_in_question(question: str, staff: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if is_role_count_question(question):
        return []

    staff = staff if staff is not None else load_staff_directory()
    if not staff or not (question or "").strip():
        return []

    best_by_id: dict[int, dict[str, Any]] = {}
    for phrase in _question_phrases(question):
        for person in staff:
            person_id = person.get("id")
            if person_id is None:
                continue
            score = _name_similarity(person, phrase)
            if score < 0.62:
                continue
            prev = best_by_id.get(person_id)
            if not prev or score > prev["match_score"]:
                best_by_id[person_id] = {**person, "match_score": round(score, 3)}

    matches = sorted(
        best_by_id.values(),
        key=lambda x: (-x["match_score"], x.get("full_name") or ""),
    )
    if len(matches) > 1:
        top = matches[0]["match_score"]
        matches = [m for m in matches if m["match_score"] >= max(0.62, top - 0.12)]
    return matches


def _tasks_for_user(tasks: list[dict[str, Any]], user_id: int) -> list[dict[str, Any]]:
    uid = int(user_id)
    return [t for t in tasks if t.get("assigned_to_id") == uid]


def _summarize_user_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    pool = [t for t in tasks if t.get("assigned_to_id") is not None]
    counts = {
        "total_assigned": len(pool),
        "delayed": sum(1 for t in pool if t.get("status") == Task.Status.DELAYED),
        "blocked": sum(1 for t in pool if t.get("status") == Task.Status.BLOCKED),
        "in_progress": sum(1 for t in pool if t.get("status") == Task.Status.IN_PROGRESS),
        "completed": sum(1 for t in pool if t.get("status") == Task.Status.COMPLETED),
        "not_started": sum(1 for t in pool if t.get("status") == Task.Status.NOT_STARTED),
        "paused": sum(1 for t in pool if t.get("status") == Task.Status.PAUSED),
    }
    return {
        **counts,
        "delayed_task_titles": [t.get("title") for t in pool if t.get("status") == Task.Status.DELAYED][:10],
        "in_progress_task_titles": [t.get("title") for t in pool if t.get("status") == Task.Status.IN_PROGRESS][:10],
    }


def format_disambiguation_reply(question: str, matches: list[dict[str, Any]]) -> str:
    lines = [
        f"I found {len(matches)} people that may match what you asked about. Which one do you mean?",
        "",
    ]
    for idx, person in enumerate(matches[:6], start=1):
        dept = f" · {person['department']}" if person.get("department") else ""
        role = person.get("role_label") or person.get("role") or "User"
        lines.append(
            f"{idx}. {person.get('full_name', 'Unknown')} — {role}{dept} ({person.get('email') or 'no email'})"
        )
    lines.extend(
        [
            "",
            "Please ask again with the full name, for example:",
            f'"{question.strip()} for {matches[0]["full_name"]}"',
        ]
    )
    return "\n".join(lines)


def try_disambiguation_reply(question: str, payload: dict[str, Any]) -> str | None:
    if is_role_count_question(question):
        return None
    # Always match against full staff records (payload staff_directory is compact for the LLM).
    matches = find_people_in_question(question, load_staff_directory())
    if len(matches) <= 1:
        return None
    return format_disambiguation_reply(question, matches)


def enrich_payload_for_question(question: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload["portal_user_counts"] = build_portal_user_counts()
    staff = load_staff_directory()
    payload["staff_directory"] = [
        {
            "id": p["id"],
            "full_name": p["full_name"],
            "role": p["role_label"],
            "email": p["email"],
            "department": p["department"],
        }
        for p in staff[:120]
    ]

    matches = find_people_in_question(question, staff)
    tasks = payload.get("tasks") or []
    payload["name_resolution"] = {
        "people_detected_in_question": len(matches),
        "tolerates_spelling_mistakes": True,
    }

    if len(matches) == 1:
        person = matches[0]
        user_tasks = _tasks_for_user(tasks, person["id"])
        assigned_summary = _summarize_user_tasks(user_tasks)
        payload["question_user_context"] = {
            "matched_person": {
                "id": person["id"],
                "full_name": person["full_name"],
                "role": person["role_label"],
                "email": person["email"],
                "department": person["department"],
                "match_confidence": person["match_score"],
            },
            "assigned_task_summary": assigned_summary,
            "instruction": (
                f"The admin question is about {person['full_name']}. "
                "Use assigned_task_summary for their delayed/blocked/in-progress task counts. "
                "Answer in plain English with their name."
            ),
        }
    elif len(matches) > 1:
        payload["question_user_context"] = {
            "ambiguous_people": [
                {
                    "id": m["id"],
                    "full_name": m["full_name"],
                    "role": m["role_label"],
                    "email": m["email"],
                }
                for m in matches[:6]
            ],
            "instruction": "Multiple people match — ask the admin which person they mean and show roles.",
        }

    return payload
