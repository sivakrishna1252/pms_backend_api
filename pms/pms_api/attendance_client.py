"""HTTP client: PMS → attendance service (read-only internal APIs)."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error, request

from django.conf import settings

from .service_auth import service_authorization_header

logger = logging.getLogger(__name__)


def _attendance_get_json(path: str, *, timeout: int = 8) -> dict[str, Any] | None:
    base_url = (getattr(settings, "ATTENDANCE_API_BASE_URL", "") or "").strip()
    authorization = service_authorization_header()
    if not base_url:
        logger.warning("Attendance call skipped (%s): ATTENDANCE_API_BASE_URL is not set.", path)
        return None
    if not authorization:
        logger.warning("Attendance call skipped (%s): PMS service token is not configured.", path)
        return None

    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    req = request.Request(
        url,
        headers={"Authorization": authorization, "Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode())
    except error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:500]
        except OSError:
            pass
        logger.warning("Attendance GET %s failed: HTTP %s — %s", url, exc.code, detail)
        return None
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Attendance GET %s failed: %s", url, exc)
        return None

    if isinstance(body, dict) and body.get("success"):
        data = body.get("data")
        return data if isinstance(data, dict) else None
    return None


def fetch_attendance_readonly_snapshot() -> dict[str, Any] | None:
    """Read-only attendance/leave snapshot for admin AI context (HTTP, then DB fallback)."""
    data = _attendance_get_json("api/internal/ai-readonly-snapshot/")
    if data:
        data["source"] = "attendance_http_api"
        return data

    from .attendance_db_bridge import attendance_snapshot_from_db

    db_data = attendance_snapshot_from_db()
    if db_data:
        logger.info("Loaded attendance AI snapshot via direct database bridge.")
    return db_data
