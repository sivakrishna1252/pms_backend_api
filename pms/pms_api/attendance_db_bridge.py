"""Direct read-only access to attendance PostgreSQL when HTTP internal API is unavailable."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db import connections
from django.utils import timezone

logger = logging.getLogger(__name__)


def _attendance_db_available() -> bool:
    return "attendance" in connections.databases


def _cursor():
    return connections["attendance"].cursor()


def _staff_name_map() -> dict[int, str]:
    """Resolve employee_id → display name from PMS default DB."""
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.first_name, u.last_name, u.email
                FROM auth_user u
                INNER JOIN pms_api_userprofile p ON p.user_id = u.id
                WHERE p.role IN ('EMPLOYEE', 'BA') AND p.status = 'ACTIVE'
                ORDER BY u.id
                """
            )
            out: dict[int, str] = {}
            for uid, first, last, email in cursor.fetchall():
                full = f"{(first or '').strip()} {(last or '').strip()}".strip()
                if full:
                    out[int(uid)] = full
                elif email:
                    out[int(uid)] = str(email).split("@")[0].replace(".", " ").title()
                else:
                    out[int(uid)] = f"Employee {uid}"
            return out
    except Exception:
        logger.exception("PMS staff name lookup failed for attendance AI bridge")
        return {}


def _name(name_by_id: dict[int, str], employee_id: int) -> str:
    return name_by_id.get(int(employee_id), f"Employee {employee_id}")


def _attendance_summary_for_date(cursor, target_date) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT COUNT(*) FROM attendance_attendancelog
        WHERE attendance_date = %s
        """,
        [target_date],
    )
    records = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*) FROM attendance_attendancelog
        WHERE attendance_date = %s AND check_in_time IS NOT NULL
        """,
        [target_date],
    )
    checked_in = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*) FROM attendance_attendancelog
        WHERE attendance_date = %s AND status = 'CHECKED_OUT'
        """,
        [target_date],
    )
    checked_out = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*) FROM attendance_attendancelog
        WHERE attendance_date = %s AND status = 'PRESENT'
        """,
        [target_date],
    )
    still_present = cursor.fetchone()[0]

    return {
        "date": target_date.isoformat(),
        "records": records,
        "checked_in": checked_in,
        "checked_out": checked_out,
        "still_present_not_checked_out": still_present,
    }


def attendance_snapshot_from_db() -> dict[str, Any] | None:
    if not _attendance_db_available():
        return None

    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    name_by_id = _staff_name_map()

    try:
        with _cursor() as cursor:
            today_summary = _attendance_summary_for_date(cursor, today)
            yesterday_summary = _attendance_summary_for_date(cursor, yesterday)

            records_today = today_summary["records"]
            checked_in_today = today_summary["checked_in"]
            checked_out_today = today_summary["checked_out"]
            still_present = today_summary["still_present_not_checked_out"]

            cursor.execute(
                """
                SELECT status, COUNT(*) FROM leaves_leaverequest
                GROUP BY status
                """
            )
            leave_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT employee_id, leave_type, from_date, to_date, status, reason
                FROM leaves_leaverequest
                WHERE status = 'PENDING'
                ORDER BY created_at DESC
                LIMIT 40
                """
            )
            pending = [
                {
                    "employee_id": row[0],
                    "employee_name": _name(name_by_id, row[0]),
                    "leave_type": row[1],
                    "from_date": row[2].isoformat(),
                    "to_date": row[3].isoformat(),
                    "status": row[4],
                    "reason_excerpt": (row[5] or "")[:200],
                }
                for row in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT employee_id, leave_type, from_date, to_date, status, reason
                FROM leaves_leaverequest
                WHERE status = 'APPROVED' AND from_date <= %s AND to_date >= %s
                ORDER BY from_date
                LIMIT 30
                """,
                [today, today],
            )
            on_leave_today = [
                {
                    "employee_id": row[0],
                    "employee_name": _name(name_by_id, row[0]),
                    "leave_type": row[1],
                    "from_date": row[2].isoformat(),
                    "to_date": row[3].isoformat(),
                    "status": row[4],
                    "reason_excerpt": (row[5] or "")[:200],
                }
                for row in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT name, holiday_date, description
                FROM leaves_holiday
                WHERE is_active = TRUE AND holiday_date >= %s
                ORDER BY holiday_date
                LIMIT 20
                """,
                [today],
            )
            holidays = [
                {
                    "name": row[0],
                    "holiday_date": row[1].isoformat(),
                    "description_excerpt": (row[2] or "")[:120],
                }
                for row in cursor.fetchall()
            ]

            since = today - timedelta(days=30)
            cursor.execute(
                """
                SELECT employee_id, attendance_date, status, check_in_time, check_out_time
                FROM attendance_attendancelog
                WHERE attendance_date >= %s
                ORDER BY attendance_date DESC, check_in_time DESC NULLS LAST
                LIMIT 80
                """,
                [since],
            )
            recent_logs = [
                {
                    "employee_id": row[0],
                    "employee_name": _name(name_by_id, row[0]),
                    "attendance_date": row[1].isoformat(),
                    "status": row[2],
                    "check_in_time": row[3].isoformat() if row[3] else None,
                    "check_out_time": row[4].isoformat() if row[4] else None,
                }
                for row in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT employee_id, attendance_date, status, check_in_time, check_out_time
                FROM attendance_attendancelog
                WHERE attendance_date = %s
                ORDER BY check_in_time DESC NULLS LAST
                LIMIT 40
                """,
                [yesterday],
            )
            yesterday_logs = [
                {
                    "employee_id": row[0],
                    "employee_name": _name(name_by_id, row[0]),
                    "attendance_date": row[1].isoformat(),
                    "status": row[2],
                    "check_in_time": row[3].isoformat() if row[3] else None,
                    "check_out_time": row[4].isoformat() if row[4] else None,
                }
                for row in cursor.fetchall()
            ]

    except Exception:
        logger.exception("Attendance DB snapshot failed")
        return None

    return {
        "source": "attendance_database",
        "as_of_date": today.isoformat(),
        "attendance_summary_today": {
            "records_today": records_today,
            "checked_in_today": checked_in_today,
            "checked_out_today": checked_out_today,
            "still_present_not_checked_out": still_present,
            "staff_count_known": len(name_by_id),
        },
        "attendance_summary_yesterday": yesterday_summary,
        "attendance_logs_yesterday": yesterday_logs,
        "leave_status_counts": {
            "pending": leave_counts.get("PENDING", 0),
            "approved": leave_counts.get("APPROVED", 0),
            "rejected": leave_counts.get("REJECTED", 0),
        },
        "employees_on_approved_leave_today": on_leave_today,
        "pending_leave_requests": pending,
        "upcoming_holidays": holidays,
        "recent_attendance_logs": recent_logs,
    }
