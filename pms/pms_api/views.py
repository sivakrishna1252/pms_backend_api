from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from html import escape
from datetime import datetime, time, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import logging
import hashlib
import json
import secrets
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenRefreshSerializer


from .ai_employee_insights import (
    try_employee_performance_reply,
    try_employee_period_report_reply,
    try_employee_task_count_reply,
)
from .ai_prompts import (
    READ_ONLY_REFUSAL,
    build_system_prompt,
    build_user_message,
    is_attendance_question,
    is_write_intent,
    try_greeting_reply,
    try_self_identity_reply,
    try_yesterday_attendance_reply,
)
from .ai_readonly_context import build_readonly_context_payload, build_readonly_context_text
from .ai_user_resolution import try_role_count_reply
from .models import (
    FileAttachment,
    Milestone,
    Notification,
    Project,
    ProjectDeadlineChangeRequest,
    Task,
    TimeLog,
    UserProfile,
    humanize_duration,
)
from .timer_state import assignee_timer_state, stop_open_timers_for_task
from .timer_logs_visibility import assignee_time_logs_queryset
from .work_history_retention import (
    WORK_HISTORY_RETENTION_MONTHS,
    apply_work_history_retention,
    visible_completed_tasks_for_user,
)
from .llm_client import LLMClientError, get_ai_provider, llm_chat, llm_health
from .sarvam_client import get_sarvam_settings
from .progress import (
    milestone_progress_data,
    project_progress_data,
    work_tracking_progress_for_tasks,
    worked_seconds_in_range,
)
from .pagination import StandardResultsSetPagination, paginate_request, unpaginated_list_response
from .permissions import IsAdmin, IsAdminOrBA, IsServiceToken, user_role
from .serializers import (
    AdminAIAskSerializer,
    AuthLoginSerializer,
    AuthResponseSerializer,
    AdminForgotPasswordRequestSerializer,
    AdminForgotPasswordVerifySerializer,
    FirstLoginRequestOTPSerializer,
    FirstLoginResendLinkSerializer,
    FirstLoginSetPasswordSerializer,
    FirstLoginTokenVerifySerializer,
    AdminPasswordResetSerializer,
    DeadlineChangeRequestSerializer,
    DeadlineChangeRejectSerializer,
    DeleteRequestSerializer,
    FileAttachmentSerializer,
    FileUploadRequestSerializer,
    MilestoneSerializer,
    MeUpdateSerializer,
    InternalNotificationCreateSerializer,
    NotificationSerializer,
    ProjectSerializer,
    TaskSerializer,
    TimeLogSerializer,
    UserCreateSerializer,
    UserUpdateSerializer,
    UserSerializer,
)


#all api response function
User = get_user_model()
logger = logging.getLogger(__name__)
ADMIN_RESET_OTP_TTL_SECONDS = 600
FIRST_LOGIN_OTP_TTL_SECONDS = 600
FIRST_LOGIN_TOKEN_TTL_SECONDS = 24 * 60 * 60


def _stop_timers_before_complete(task, actor):
    if (
        user_role(actor) == UserProfile.Roles.EMPLOYEE
        or task.assigned_to_id == actor.id
    ):
        stop_open_timers_for_task(task, user=actor)
    else:
        stop_open_timers_for_task(task)


def _render_email_html(subject, greeting, intro_text, detail_rows, footer_note="Regards,<br>PMS Team"):
    def _html_value(value):
        text = str(value or "").strip()
        if text.startswith("http://") or text.startswith("https://"):
            href = escape(text, quote=True)
            label = escape(text)
            return (
                f'<a href="{href}" target="_blank" rel="noopener noreferrer" '
                f'style="color:#2563eb;text-decoration:underline;word-break:break-all;">{label}</a>'
            )
        return escape(text)

    detail_items = "".join(
        f"""
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;font-weight:600;vertical-align:top;width:180px;">{escape(str(label))}</td>
          <td style="padding:8px 0;color:#111827;font-size:14px;">{_html_value(value)}</td>
        </tr>
        """
        for label, value in detail_rows
    )
    return f"""
    <html>
      <body style="margin:0;padding:0;background-color:#f3f4f6;font-family:Arial,sans-serif;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f3f4f6;padding:24px 0;">
          <tr>
            <td align="center">
              <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
                <tr>
                  <td style="background:#2563eb;color:#ffffff;padding:20px 24px;font-size:20px;font-weight:700;">{escape(subject)}</td>
                </tr>
                <tr>
                  <td style="padding:24px;">
                    <p style="margin:0 0 12px;color:#111827;font-size:15px;">{escape(greeting)}</p>
                    <p style="margin:0 0 18px;color:#374151;font-size:14px;line-height:1.6;">{escape(intro_text)}</p>
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb;padding:8px 0;">
                      {detail_items}
                    </table>
                    <p style="margin:18px 0 0;color:#374151;font-size:14px;line-height:1.6;">{footer_note}</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """


def _send_styled_email(subject, recipient_list, greeting, intro_text, detail_rows, footer_note="Regards,\nPMS Team"):
    message_lines = [greeting, "", intro_text, ""]
    for label, value in detail_rows:
        message_lines.append(f"{label}: {value}")
    message_lines.extend(["", footer_note])
    plain_text = "\n".join(message_lines)
    html_message = _render_email_html(subject, greeting, intro_text, detail_rows, footer_note=footer_note.replace("\n", "<br>"))
    send_mail(
        subject=subject,
        message=plain_text,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=recipient_list,
        fail_silently=False,
        html_message=html_message,
    )

def api_response(success, message, code, data=None):
    return Response(
        {"success": success, "message": message, "code": code, "data": data},
        status=code,
    )


def _deadline_change_notification_message(
    *,
    kind,
    item_name: str,
    requester,
    old_deadline,
    new_deadline,
    reason: str = "",
) -> str:
    """In-app notification text: who asked, entity name, old date → new date (aligned with email)."""

    def _fmt(d):
        if d is None:
            return "none"
        if hasattr(d, "isoformat"):
            return d.isoformat()
        return str(d)

    who = (requester.get_full_name() or "").strip() or getattr(requester, "email", None) or "User"
    old_s = _fmt(old_deadline)
    new_s = _fmt(new_deadline)
    if kind == "task":
        base = (
            f"{who} requested changing the deadline for task '{item_name}' "
            f"from {old_s} to {new_s}."
        )
    else:
        base = (
            f"{who} requested changing the project '{item_name}' deadline "
            f"from {old_s} to {new_s}."
        )
    reason = (reason or "").strip()
    if reason:
        return f"{base} Reason: {reason}"
    return base


def _deadline_change_details_json(old_deadline, new_deadline, **extra):
    """Structured fields for notification UI (survives alongside message text)."""

    def _fmt(d):
        if d is None:
            return None
        if hasattr(d, "isoformat"):
            return d.isoformat()
        return str(d)

    payload = {
        "deadline_from": _fmt(old_deadline),
        "deadline_to": _fmt(new_deadline),
    }
    payload.update(extra)
    return payload


def _user_display_name(user) -> str:
    if not user:
        return "User"
    return (user.get_full_name() or "").strip() or getattr(user, "email", None) or "User"


def _get_pending_project_deadline_request(project):
    return (
        ProjectDeadlineChangeRequest.objects.filter(
            project=project,
            status=ProjectDeadlineChangeRequest.Status.PENDING,
        )
        .select_related("requested_by", "project")
        .order_by("-created_at")
        .first()
    )


def _project_deadline_request_payload(change_request):
    project = change_request.project
    requester = change_request.requested_by
    reviewer = change_request.reviewed_by
    status_value = change_request.status
    return {
        "id": change_request.id,
        "request_id": change_request.id,
        "project_id": project.id,
        "project_name": project.name,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "status": status_value,
        "can_respond": status_value == ProjectDeadlineChangeRequest.Status.PENDING,
        "requested_by_name": _user_display_name(requester),
        "requested_on": change_request.created_at.isoformat() if change_request.created_at else None,
        "current_deadline": change_request.current_deadline.isoformat(),
        "deadline_from": change_request.current_deadline.isoformat(),
        "proposed_deadline": change_request.requested_deadline.isoformat(),
        "deadline_to": change_request.requested_deadline.isoformat(),
        "new_deadline": change_request.requested_deadline.isoformat(),
        "days_extension": max(
            0,
            (change_request.requested_deadline - change_request.current_deadline).days,
        ),
        "reason": (change_request.reason or "").strip(),
        "reviewed_by_name": _user_display_name(reviewer) if reviewer else None,
        "reviewed_at": change_request.reviewed_at.isoformat() if change_request.reviewed_at else None,
        "rejection_reason": (change_request.rejection_reason or "").strip(),
    }


def _already_reviewed_deadline_response(project):
    resolved = (
        ProjectDeadlineChangeRequest.objects.filter(project=project)
        .exclude(status=ProjectDeadlineChangeRequest.Status.PENDING)
        .select_related("reviewed_by", "requested_by", "project")
        .order_by("-reviewed_at", "-id")
        .first()
    )
    if not resolved:
        return api_response(
            False,
            "No pending deadline change request for this project.",
            status.HTTP_400_BAD_REQUEST,
        )
    reviewer_name = _user_display_name(resolved.reviewed_by)
    status_label = resolved.status.lower()
    return api_response(
        False,
        f"This deadline change request was already {status_label} by {reviewer_name}.",
        status.HTTP_409_CONFLICT,
        _project_deadline_request_payload(resolved),
    )


def _complete_project_deadline_review(project, reviewer, *, approved, rejection_reason=""):
    with transaction.atomic():
        change_request = (
            ProjectDeadlineChangeRequest.objects.select_for_update()
            .filter(
                project=project,
                status=ProjectDeadlineChangeRequest.Status.PENDING,
            )
            .select_related("requested_by", "project")
            .order_by("-created_at")
            .first()
        )
        if not change_request:
            return None, _already_reviewed_deadline_response(project)

        change_request.status = (
            ProjectDeadlineChangeRequest.Status.APPROVED
            if approved
            else ProjectDeadlineChangeRequest.Status.REJECTED
        )
        change_request.reviewed_by = reviewer
        change_request.reviewed_at = timezone.now()
        update_fields = ["status", "reviewed_by", "reviewed_at", "updated_at"]
        if not approved:
            change_request.rejection_reason = rejection_reason or ""
            update_fields.append("rejection_reason")
        change_request.save(update_fields=update_fields)

        if approved:
            project.deadline = change_request.requested_deadline
            project.save(update_fields=["deadline", "updated_at"])

    _notify_project_deadline_decision(
        change_request,
        approved=approved,
        reviewer=reviewer,
        rejection_reason=rejection_reason,
    )
    return change_request, None


def _notify_project_deadline_decision(change_request, *, approved: bool, reviewer, rejection_reason=""):
    project = change_request.project
    requester = change_request.requested_by
    reviewer_name = _user_display_name(reviewer)
    decision_word = "approved" if approved else "rejected"
    new_deadline_text = (
        change_request.requested_deadline.isoformat()
        if approved and change_request.requested_deadline
        else change_request.current_deadline.isoformat()
    )

    if requester:
        ba_message = (
            f"{reviewer_name} {decision_word} your deadline change request for project "
            f"'{project.name}' ({change_request.current_deadline.isoformat()} → "
            f"{change_request.requested_deadline.isoformat()})."
        )
        if not approved and (rejection_reason or "").strip():
            ba_message = f"{ba_message} Reason: {rejection_reason.strip()}"
        Notification.objects.create(
            user=requester,
            type="PROJECT_DEADLINE_CHANGE_DECISION",
            title=f"Project deadline change {decision_word}",
            message=ba_message,
            details=_deadline_change_details_json(
                change_request.current_deadline,
                change_request.requested_deadline,
                request_id=change_request.id,
                project_id=project.id,
                project_name=project.name,
                approved=approved,
                reviewer_name=reviewer_name,
                rejection_reason=(rejection_reason or "").strip(),
            ),
            ref_type=Notification.RefType.PROJECT,
            ref_id=project.id,
        )

    admin_users = User.objects.filter(
        profile__role=UserProfile.Roles.ADMIN,
        profile__status=UserProfile.Status.ACTIVE,
    ).exclude(id=reviewer.id)
    requester_name = _user_display_name(requester)
    for admin_user in admin_users:
        Notification.objects.create(
            user=admin_user,
            type="PROJECT_DEADLINE_CHANGE_DECISION",
            title=f"Project deadline change {decision_word}",
            message=(
                f"{reviewer_name} {decision_word} {requester_name}'s deadline change request "
                f"for project '{project.name}'."
            ),
            details=_deadline_change_details_json(
                change_request.current_deadline,
                change_request.requested_deadline,
                request_id=change_request.id,
                project_id=project.id,
                project_name=project.name,
                approved=approved,
                requester_name=requester_name,
                reviewer_name=reviewer_name,
            ),
            ref_type=Notification.RefType.PROJECT,
            ref_id=project.id,
        )

    if requester and requester.email:
        subject = f"Project Deadline Change {decision_word.title()}: {project.name}"
        greeting = f"Hi {requester.first_name or requester.username},"
        intro_text = f"An admin has {decision_word} your project deadline change request."
        detail_rows = [
            ("Project", project.name),
            ("Reviewed By", reviewer_name),
            ("Decision", decision_word.title()),
            ("Previous Deadline", change_request.current_deadline),
            ("Requested Deadline", change_request.requested_deadline),
            ("Effective Deadline", new_deadline_text),
        ]
        if not approved and (rejection_reason or "").strip():
            detail_rows.append(("Rejection Reason", rejection_reason.strip()))
        try:
            _send_styled_email(subject, [requester.email], greeting, intro_text, detail_rows)
        except Exception:
            logger.exception(
                "Failed project deadline decision email for request %s",
                change_request.id,
            )


def apply_automatic_task_status_rules():
    """Keep derived task states in sync.
    Rule: overdue non-final tasks automatically become DELAYED.
    """
    today = timezone.localdate()
    Task.objects.filter(
        deadline__isnull=False,
        deadline__lt=today,
        status__in=[Task.Status.NOT_STARTED, Task.Status.IN_PROGRESS, Task.Status.PAUSED],
    ).update(status=Task.Status.DELAYED)


def _task_project_name(task) -> str:
    if not task or not getattr(task, "project_id", None):
        return ""
    project = getattr(task, "project", None)
    return project.name if project else ""


def _task_milestone_name(task):
    if not task or not getattr(task, "milestone_id", None):
        return None
    milestone = getattr(task, "milestone", None)
    return milestone.name if milestone else None


def _recent_activity_window_start():
    now_local = timezone.localtime()
    day_start = datetime.combine(now_local.date(), time.min, tzinfo=now_local.tzinfo)
    reset_time = datetime.combine(now_local.date(), time(hour=22), tzinfo=now_local.tzinfo)
    return reset_time if now_local >= reset_time else day_start


def _build_notification_recent_activity(user, window_start=None):
    """Include in-app alerts (e.g. employee self-created tasks) on dashboard feeds."""
    if user is None or not getattr(user, "is_authenticated", True):
        return []
    events = []

    self_created_qs = Notification.objects.filter(user=user, type="TASK_SELF_CREATED")
    if window_start is not None:
        self_created_qs = self_created_qs.filter(created_at__gte=window_start)
    self_created = list(self_created_qs.order_by("-created_at")[:40])
    task_ids = [n.ref_id for n in self_created if n.ref_id]
    tasks_by_id = {
        t.id: t
        for t in Task.objects.select_related("project").filter(id__in=task_ids)
    }
    for notification in self_created:
        details = notification.details or {}
        employee_name = details.get("employee_name") or "Employee"
        task = tasks_by_id.get(notification.ref_id) if notification.ref_id else None
        project_name = _task_project_name(task) if task else ""
        events.append(
            {
                "action": "SELF_CREATED",
                "employee_name": employee_name,
                "task_id": notification.ref_id or 0,
                "task_title": task.title if task else notification.title,
                "project_name": project_name or "No project",
                "timestamp": notification.created_at,
            }
        )

    role = user_role(user)
    if role == UserProfile.Roles.ADMIN:
        events.extend(_build_pending_project_deadline_activity(user))
        events.extend(_build_resolved_project_deadline_activity(user, window_start))

    return events


def _build_pending_project_deadline_activity(user):
    """Pending BA project deadline requests — any admin can approve or reject."""
    if user_role(user) != UserProfile.Roles.ADMIN:
        return []

    pending_requests = (
        ProjectDeadlineChangeRequest.objects.filter(
            status=ProjectDeadlineChangeRequest.Status.PENDING,
        )
        .select_related("project", "requested_by")
        .order_by("-created_at")[:20]
    )
    events = []
    for change_request in pending_requests:
        requester_name = _user_display_name(change_request.requested_by)
        events.append(
            {
                "action": "DEADLINE_REQUEST",
                "employee_name": requester_name,
                "task_id": 0,
                "task_title": "",
                "project_id": change_request.project_id,
                "project_name": change_request.project.name,
                "request_id": change_request.id,
                "deadline_from": change_request.current_deadline.isoformat(),
                "deadline_to": change_request.requested_deadline.isoformat(),
                "reason": (change_request.reason or "").strip(),
                "can_respond": True,
                "timestamp": change_request.created_at,
            }
        )
    return events


def _build_resolved_project_deadline_activity(user, window_start=None):
    """Show who approved/rejected a project deadline request in recent activity."""
    role = user_role(user)
    if role not in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}:
        return []

    qs = ProjectDeadlineChangeRequest.objects.exclude(
        status=ProjectDeadlineChangeRequest.Status.PENDING,
    ).select_related("project", "requested_by", "reviewed_by")
    if window_start is not None:
        qs = qs.filter(reviewed_at__gte=window_start)
    if role == UserProfile.Roles.BA:
        qs = qs.filter(requested_by=user)

    events = []
    for change_request in qs.order_by("-reviewed_at")[:20]:
        reviewer_name = _user_display_name(change_request.reviewed_by)
        requester_name = _user_display_name(change_request.requested_by)
        approved = change_request.status == ProjectDeadlineChangeRequest.Status.APPROVED
        events.append(
            {
                "action": "DEADLINE_APPROVED" if approved else "DEADLINE_REJECTED",
                "employee_name": reviewer_name,
                "task_id": 0,
                "task_title": "",
                "project_id": change_request.project_id,
                "project_name": change_request.project.name,
                "request_id": change_request.id,
                "requester_name": requester_name,
                "deadline_from": change_request.current_deadline.isoformat(),
                "deadline_to": change_request.requested_deadline.isoformat(),
                "can_respond": False,
                "timestamp": change_request.reviewed_at or change_request.updated_at,
            }
        )
    return events


def _merge_recent_activity_events(*event_lists, limit=20):
    events = []
    for event_list in event_lists:
        events.extend(event_list or [])
    events.sort(key=lambda item: item["timestamp"], reverse=True)
    return events[:limit]


def _resolve_aggregate_status(tasks_qs):
    """Derive rollup status from task states and work-tracking progress."""
    today = timezone.localdate()
    total = tasks_qs.count()
    if total == 0:
        return "NOT_STARTED"

    progress_snapshot = work_tracking_progress_for_tasks(tasks_qs)
    completed = progress_snapshot.get("completed_task_count", 0)
    progress_pct = progress_snapshot.get("progress_percent") or 0

    if completed >= total:
        return "COMPLETED"

    delayed = tasks_qs.filter(
        Q(status=Task.Status.DELAYED)
        | (Q(deadline__isnull=False) & Q(deadline__lt=today) & ~Q(status=Task.Status.COMPLETED))
    ).exists()
    if delayed:
        return "DELAYED"

    active = tasks_qs.filter(status__in=[Task.Status.IN_PROGRESS, Task.Status.PAUSED]).exists()
    if active:
        return "IN_PROGRESS"

    # Partial work or completed tasks without an explicit IN_PROGRESS flag
    if progress_pct > 0 or completed > 0:
        return "IN_PROGRESS"

    return "NOT_STARTED"


def _milestone_status_from_aggregate(aggregate_state: str) -> str:
    if aggregate_state == "COMPLETED":
        return Milestone.Status.COMPLETED
    if aggregate_state == "DELAYED":
        return Milestone.Status.DELAYED
    if aggregate_state == "IN_PROGRESS":
        return Milestone.Status.IN_PROGRESS
    return Milestone.Status.NOT_STARTED


def _project_status_from_aggregate(aggregate_state: str) -> str:
    if aggregate_state == "COMPLETED":
        return Project.Status.COMPLETED
    if aggregate_state == "DELAYED":
        return Project.Status.DELAYED
    if aggregate_state == "IN_PROGRESS":
        return Project.Status.ACTIVE
    return Project.Status.PLANNED


def sync_parent_statuses_for_project(project_id: int) -> None:
    """Recompute milestone and project status from all tasks in the project."""
    if not project_id:
        return

    for milestone in Milestone.objects.filter(project_id=project_id):
        ms_tasks = Task.objects.filter(milestone_id=milestone.id)
        mapped_ms_status = _milestone_status_from_aggregate(_resolve_aggregate_status(ms_tasks))
        if milestone.status != mapped_ms_status:
            milestone.status = mapped_ms_status
            milestone.save(update_fields=["status"])

    project = Project.objects.filter(id=project_id).first()
    if not project:
        return

    prj_tasks = Task.objects.filter(project_id=project_id)
    mapped_project_status = _project_status_from_aggregate(_resolve_aggregate_status(prj_tasks))
    if project.status != mapped_project_status:
        project.status = mapped_project_status
        project.save(update_fields=["status"])


def _sync_parent_statuses_for_task(task: Task) -> None:
    """Keep milestone/project statuses aligned with underlying tasks and work progress."""
    if task.project_id:
        sync_parent_statuses_for_project(task.project_id)


def build_admin_overview_payload(project_id=None, milestone_id=None, task_id=None):
    apply_automatic_task_status_rules()
    tasks_qs = Task.objects.select_related("project", "milestone", "assigned_to", "created_by").all()
    if project_id:
        tasks_qs = tasks_qs.filter(project_id=project_id)
    if milestone_id:
        tasks_qs = tasks_qs.filter(milestone_id=milestone_id)
    if task_id:
        tasks_qs = tasks_qs.filter(id=task_id)

    task_ids = list(tasks_qs.values_list("id", flat=True))
    project_ids = list(tasks_qs.values_list("project_id", flat=True).distinct())
    milestone_ids = list(tasks_qs.exclude(milestone_id__isnull=True).values_list("milestone_id", flat=True).distinct())

    project_qs = Project.objects.filter(id__in=project_ids)
    milestone_qs = Milestone.objects.filter(id__in=milestone_ids)

    ba_rows = list(
        User.objects.filter(profile__role=UserProfile.Roles.BA)
        .annotate(
            tasks_created_count=Count("tasks_created", filter=Q(tasks_created__id__in=task_ids), distinct=True),
            tasks_completed_count=Count(
                "tasks_created",
                filter=Q(tasks_created__status=Task.Status.COMPLETED, tasks_created__id__in=task_ids),
                distinct=True,
            ),
            active_tasks_count=Count(
                "tasks_created",
                filter=Q(tasks_created__status=Task.Status.IN_PROGRESS, tasks_created__id__in=task_ids),
                distinct=True,
            ),
            employees_assigned_count=Count("tasks_created__assigned_to", filter=Q(tasks_created__id__in=task_ids), distinct=True),
        )
        .values(
            "id",
            "first_name",
            "last_name",
            "email",
            "tasks_created_count",
            "tasks_completed_count",
            "active_tasks_count",
            "employees_assigned_count",
        )
        .order_by("first_name", "id")
    )
    ba_summary = [
        {
            "id": row["id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "email": row["email"],
            "tasks_created": row["tasks_created_count"],
            "tasks_completed": row["tasks_completed_count"],
            "active_tasks": row["active_tasks_count"],
            "employees_assigned": row["employees_assigned_count"],
        }
        for row in ba_rows
    ]

    employee_rows = list(
        User.objects.filter(profile__role=UserProfile.Roles.EMPLOYEE)
        .annotate(
            assigned_tasks_count=Count("tasks_assigned", filter=Q(tasks_assigned__id__in=task_ids), distinct=True),
            completed_tasks_count=Count(
                "tasks_assigned",
                filter=Q(tasks_assigned__status=Task.Status.COMPLETED, tasks_assigned__id__in=task_ids),
                distinct=True,
            ),
            in_progress_tasks_count=Count(
                "tasks_assigned",
                filter=Q(tasks_assigned__status=Task.Status.IN_PROGRESS, tasks_assigned__id__in=task_ids),
                distinct=True,
            ),
            blocked_tasks_count=Count(
                "tasks_assigned",
                filter=Q(tasks_assigned__status=Task.Status.BLOCKED, tasks_assigned__id__in=task_ids),
                distinct=True,
            ),
            total_time_spent_seconds=Coalesce(
                Sum("tasks_assigned__total_time_spent_seconds", filter=Q(tasks_assigned__id__in=task_ids)), 0
            ),
            active_timers_count=Count(
                "time_logs",
                filter=Q(time_logs__end_time__isnull=True, time_logs__task_id__in=task_ids),
                distinct=True,
            ),
        )
        .values(
            "id",
            "first_name",
            "last_name",
            "email",
            "assigned_tasks_count",
            "completed_tasks_count",
            "in_progress_tasks_count",
            "blocked_tasks_count",
            "total_time_spent_seconds",
            "active_timers_count",
        )
        .order_by("first_name", "id")
    )
    employee_summary = [
        {
            "id": row["id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "email": row["email"],
            "assigned_tasks": row["assigned_tasks_count"],
            "completed_tasks": row["completed_tasks_count"],
            "in_progress_tasks": row["in_progress_tasks_count"],
            "blocked_tasks": row["blocked_tasks_count"],
            "total_time_spent_seconds": row["total_time_spent_seconds"],
            "total_time_spent_display": humanize_duration(row["total_time_spent_seconds"]),
            "active_timers": row["active_timers_count"],
        }
        for row in employee_rows
    ]

    task_status_counts = {
        "not_started": tasks_qs.filter(status=Task.Status.NOT_STARTED).count(),
        "in_progress": tasks_qs.filter(status=Task.Status.IN_PROGRESS).count(),
        "paused": tasks_qs.filter(status=Task.Status.PAUSED).count(),
        "completed": tasks_qs.filter(status=Task.Status.COMPLETED).count(),
        "delayed": tasks_qs.filter(status=Task.Status.DELAYED).count(),
        "blocked": tasks_qs.filter(status=Task.Status.BLOCKED).count(),
    }

    return {
        "filters": {"project_id": project_id, "milestone_id": milestone_id, "task_id": task_id},
        "overview": {
            "users_count": User.objects.filter(profile__role__in=[UserProfile.Roles.ADMIN, UserProfile.Roles.BA, UserProfile.Roles.EMPLOYEE]).count(),
            "ba_count": User.objects.filter(profile__role=UserProfile.Roles.BA).count(),
            "employee_count": User.objects.filter(profile__role=UserProfile.Roles.EMPLOYEE).count(),
            "projects_count": project_qs.count() if project_id or milestone_id or task_id else Project.objects.count(),
            "tasks_count": tasks_qs.count(),
            "active_timers": TimeLog.objects.filter(end_time__isnull=True, task_id__in=task_ids).count(),
        },
        "task_status_counts": task_status_counts,
        "ba_summary": ba_summary,
        "employee_summary": employee_summary,
        "projects": [
            {
                "id": project.id,
                "name": project.name,
                "status": project.status,
                "start_date": project.start_date,
                "deadline": project.deadline,
                "description_excerpt": (project.description or "")[:500],
                "created_by_id": project.created_by_id,
                "created_by_name": project.created_by.get_full_name().strip() or project.created_by.email,
            }
            for project in project_qs
        ],
        "milestones": [
            {
                "id": milestone.id,
                "milestone_no": milestone.milestone_no,
                "name": milestone.name,
                "project_id": milestone.project_id,
                "project_name": milestone.project.name,
                "status": milestone.status,
                "start_date": milestone.start_date,
                "end_date": milestone.end_date,
                "created_by_id": milestone.created_by_id,
                "created_by_name": milestone.created_by.get_full_name().strip() or milestone.created_by.email,
            }
            for milestone in milestone_qs
        ],
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "project_id": task.project_id,
                "project_name": _task_project_name(task),
                "milestone_id": task.milestone_id,
                "milestone_no": task.milestone.milestone_no if task.milestone else None,
                "milestone_name": _task_milestone_name(task),
                "status": task.status,
                "deadline": task.deadline,
                "created_by_id": task.created_by_id,
                "created_by_name": task.created_by.get_full_name().strip() or task.created_by.email,
                "assigned_to_id": task.assigned_to_id,
                "assigned_to_name": (
                    (task.assigned_to.get_full_name().strip() or task.assigned_to.email) if task.assigned_to else None
                ),
                "total_time_spent_seconds": task.total_time_spent_seconds,
                "total_time_spent_display": humanize_duration(task.total_time_spent_seconds),
            }
            for task in tasks_qs.order_by("-updated_at")
        ],
    }

def send_task_assignment_email(employee, task):
    subject = f"Task Assigned: {task.title}"
    greeting = f"Hi {employee.first_name or employee.username},"
    intro_text = "A task has been assigned to you. Please review the task details below."
    detail_rows = [
        ("Task", task.title),
        ("Description", task.description or "N/A"),
        ("Status", task.status),
    ]
    try:
        _send_styled_email(subject, [employee.email], greeting, intro_text, detail_rows)
    except Exception:
        logger.exception("Failed to send task assignment email to %s for task %s", employee.email, task.id)

def send_admin_reset_otp_email(user, otp):
    if not user.email:
        return
    subject = "PMS Password Reset OTP"
    greeting = f"Hi {user.first_name or user.username},"
    intro_text = "Use the OTP below to reset your account password."
    detail_rows = [
        ("OTP", otp),
        ("Valid For", f"{ADMIN_RESET_OTP_TTL_SECONDS // 60} minutes"),
    ]
    try:
        _send_styled_email(
            subject,
            [user.email],
            greeting,
            intro_text,
            detail_rows,
            footer_note="If you did not request this, please ignore this email.\n\nRegards,\nPMS Team",
        )
    except Exception:
        logger.exception("Failed to send admin reset OTP email to %s", user.email)


def send_first_login_otp_email(user, otp):
    if not user.email:
        return
    subject = "PMS First Login OTP"
    greeting = f"Hi {user.first_name or user.username},"
    intro_text = "Use the OTP below to complete first-time login and set your password."
    detail_rows = [
        ("OTP", otp),
        ("Valid For", f"{FIRST_LOGIN_OTP_TTL_SECONDS // 60} minutes"),
    ]
    try:
        _send_styled_email(
            subject,
            [user.email],
            greeting,
            intro_text,
            detail_rows,
            footer_note="If you did not expect this, please ignore this email.\n\nRegards,\nPMS Team",
        )
    except Exception:
        logger.exception("Failed to send first login OTP email to %s", user.email)


def send_user_welcome_email(user, raw_password):
    if not user.email:
        return
    subject = "Your PMS account is created"
    greeting = f"Hi {user.first_name or user.username},"
    intro_text = "Your Project Management System account has been created successfully."
    detail_rows = [
        ("Login Email", user.email),
        ("Password", raw_password),
    ]
    try:
        _send_styled_email(
            subject,
            [user.email],
            greeting,
            intro_text,
            detail_rows,
            footer_note="Please login using these credentials.\n\nRegards,\nPMS Team",
        )
    except Exception:
        logger.exception("Failed to send welcome email to %s", user.email)


def send_user_first_login_email(user):
    if not user.email:
        return
    subject = "Welcome to Apparatus Solutions PMS account"
    greeting = f"Hi {user.first_name or user.username},"
    first_login_url = getattr(
        settings,
        "FRONTEND_FIRST_LOGIN_URL",
        "http://nexus.aspune.cloud/auth/activate-account",
    )
    parsed = urlsplit(first_login_url)
    existing_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.first_login_token_hash = token_hash
    profile.first_login_token_expires_at = timezone.now() + timedelta(seconds=FIRST_LOGIN_TOKEN_TTL_SECONDS)
    profile.save(update_fields=["first_login_token_hash", "first_login_token_expires_at"])
    existing_query["token"] = token
    first_login_url = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(existing_query), parsed.fragment)
    )
    intro_text = (
        "Welcome to Apparatus Solutions PMS account. Your account has been created successfully. "
        "Click the link below to set your password."
    )
    detail_rows = [
        ("Login Email", user.email),
        ("First Login URL", first_login_url),
        ("Link Valid For", "24 hours"),
    ]
    try:
        _send_styled_email(
            subject,
            [user.email],
            greeting,
            intro_text,
            detail_rows,
            footer_note=(
                "This link expires in 24 hours. If expired, request a new activation link from the activation page."
                "\n\nRegards,\nPMS Team"
            ),
        )
    except Exception:
        logger.exception("Failed to send first login email to %s", user.email)

def send_user_password_changed_email(user, raw_password):
    if not user.email:
        return
    subject = "Your PMS password has been updated"
    greeting = f"Hi {user.first_name or user.username},"
    intro_text = "Your Project Management System password was changed by an Admin user."
    detail_rows = [
        ("Login Email", user.email),
        ("New Password", raw_password),
    ]
    try:
        _send_styled_email(
            subject,
            [user.email],
            greeting,
            intro_text,
            detail_rows,
            footer_note="Please login using your updated credentials.\n\nRegards,\nPMS Team",
        )
    except Exception:
        logger.exception("Failed to send password change email to %s", user.email)


def _task_completion_recipient(task):
    if task.is_self_created and task.supervisor_id:
        return task.supervisor
    return task.created_by


def send_task_completed_email(task, completed_by):
    recipient = _task_completion_recipient(task)
    if not recipient or not recipient.email:
        return
    completed_by_name = completed_by.get_full_name().strip() or completed_by.email
    if task.is_self_created and task.supervisor_id:
        subject = f"Employee Self-Created Task Completed: {task.title}"
        greeting = f"Hi {recipient.first_name or recipient.username},"
        intro_text = "An employee has completed a self-created task you were notified about."
    else:
        subject = f"Task Completed: {task.title}"
        greeting = f"Hi {recipient.first_name or recipient.username},"
        intro_text = "A task created by you is now marked as completed."
    detail_rows = [
        ("Task", task.title),
        ("Completed By", completed_by_name),
        ("Current Status", task.status),
    ]
    try:
        _send_styled_email(subject, [recipient.email], greeting, intro_text, detail_rows)
    except Exception:
        logger.exception("Failed to send task completion email for task %s", task.id)


def send_task_deadline_change_request_email(task, employee, owner, new_deadline=None, reason=""):
    if not owner or not owner.email:
        return
    employee_name = employee.get_full_name().strip() or employee.email
    subject = f"Task Deadline Change Request: {task.title}"
    greeting = f"Hi {owner.first_name or owner.username},"
    intro_text = "An employee has requested a deadline change for a task."
    detail_rows = [
        ("Task", task.title),
        ("Requested By", employee_name),
        ("Current Deadline", task.deadline or "N/A"),
        ("Requested Deadline", new_deadline or "Not provided"),
        ("Reason", reason or "No reason provided"),
    ]
    try:
        _send_styled_email(
            subject,
            [owner.email],
            greeting,
            intro_text,
            detail_rows,
            footer_note="Please review and update the deadline if needed.\n\nRegards,\nPMS Team",
        )
    except Exception:
        logger.exception("Failed task deadline request email for task %s", task.id)


def send_task_self_created_email(task, employee, supervisor):
    if not supervisor or not supervisor.email:
        return
    employee_name = employee.get_full_name().strip() or employee.email
    subject = f"Employee Self-Created Task: {task.title}"
    greeting = f"Hi {supervisor.first_name or supervisor.username},"
    intro_text = "An employee created their own task and selected you for review."
    detail_rows = [
        ("Task", task.title),
        ("Created By", employee_name),
        ("Project", task.project.name if task.project_id else "Not linked"),
        ("Milestone", task.milestone.name if task.milestone_id else "Not linked"),
        ("Expected Date", task.deadline or "Not set"),
    ]
    try:
        _send_styled_email(
            subject,
            [supervisor.email],
            greeting,
            intro_text,
            detail_rows,
            footer_note="Please review the task when available.\n\nRegards,\nPMS Team",
        )
    except Exception:
        logger.exception("Failed self-created task email for task %s", task.id)


def _task_deadline_change_recipient(task):
    if task.is_self_created and task.supervisor_id:
        return task.supervisor
    return task.created_by


def _admin_mail_recipients():
    return list(getattr(settings, "ADMIN_MAIL_RECIPIENTS", []) or [])


def send_project_change_request_email(project, requester, admin_emails, request_type, new_deadline=None, reason=""):
    admin_emails = _admin_mail_recipients()
    if not admin_emails:
        return
    requester_name = requester.get_full_name().strip() or requester.email
    subject = f"Project {request_type} Request: {project.name}"
    greeting = "Hi Admin Team,"
    intro_text = f"A BA has submitted a project {request_type.lower()} request."
    detail_rows = [
        ("Project", project.name),
        ("Requested By", requester_name),
        ("Current Deadline", project.deadline),
        ("Requested Deadline", new_deadline or "Not applicable"),
        ("Reason", reason or "No reason provided"),
    ]
    try:
        _send_styled_email(
            subject,
            admin_emails,
            greeting,
            intro_text,
            detail_rows,
            footer_note="Please review and take action.\n\nRegards,\nPMS Team",
        )
    except Exception:
        logger.exception("Failed project %s request email for project %s", request_type, project.id)






#login api view
@extend_schema(tags=["Common APIs"])
class LoginAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=AuthLoginSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = AuthLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        profile, _ = UserProfile.objects.get_or_create(user=user)
        # Manually managed admin accounts (Django admin/superuser/staff) should
        # not be blocked by first-login onboarding flow.
        is_manual_admin_account = bool(user.is_superuser or user.is_staff)
        if not profile.password_set and not is_manual_admin_account:
            return api_response(
                False,
                "First-time login requires OTP verification and password setup.",
                status.HTTP_403_FORBIDDEN,
                {"first_login_required": True, "email": user.email},
            )
        if is_manual_admin_account and not profile.password_set:
            profile.password_set = True
            profile.save(update_fields=["password_set"])
        payload = AuthResponseSerializer.build(user)
        return api_response(True, "Login successful.", status.HTTP_200_OK, payload)

@extend_schema(tags=["Common APIs"])
class RefreshAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return api_response(True, "Token refreshed successfully.", status.HTTP_200_OK, serializer.validated_data)




#me api view
@extend_schema(tags=["Common APIs"])
class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        UserProfile.objects.get_or_create(user=request.user)
        return api_response(True, "User profile fetched.", status.HTTP_200_OK, UserSerializer(request.user).data)

    @extend_schema(request=MeUpdateSerializer, responses={200: OpenApiTypes.OBJECT})
    def patch(self, request):
        serializer = MeUpdateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.update(request.user, serializer.validated_data)
        payload = UserSerializer(user).data
        payload["password_updated"] = bool(serializer.validated_data.get("new_password"))
        message = "Profile updated successfully."
        if payload["password_updated"]:
            message = "Profile and password updated successfully."
        return api_response(True, message, status.HTTP_200_OK, payload)




#user view set pagination
@extend_schema(tags=["Admin APIs"])
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("-date_joined")
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Portal list: hide Django admin / staff accounts; only app-managed users.
        return super().get_queryset().filter(is_superuser=False, is_staff=False)

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in {"update", "partial_update"}:
            return UserUpdateSerializer
        return UserSerializer

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        if self.action == "supervisors":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsAdmin()]

    def create(self, request, *args, **kwargs):
        serializer = UserCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        payload = UserSerializer(user).data
        send_user_first_login_email(user)
        message = "User created successfully. First-login mail sent successfully."
        payload["mail_triggered"] = True
        payload["first_login_required"] = True
        return api_response(True, message, status.HTTP_201_CREATED, payload)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        raw_password = serializer.validated_data.get("password")
        payload = UserSerializer(user).data
        message = "User updated successfully."
        if raw_password:
            send_user_password_changed_email(user, raw_password)
            message = "User updated successfully. Mail sent successfully."
            payload["mail_triggered"] = True
        else:
            payload["mail_triggered"] = False
        return api_response(True, message, status.HTTP_200_OK, payload)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()

        if user.id == request.user.id:
            return api_response(
                False,
                "You cannot delete your own account while logged in.",
                status.HTTP_400_BAD_REQUEST,
            )

        user_id = user.id
        user_label = user.get_full_name().strip() or user.email or f"user #{user_id}"
        try:
            self.perform_destroy(user)
        except ProtectedError:
            return api_response(
                False,
                (
                    f"Cannot delete {user_label} because existing projects, milestones, or tasks "
                    "still reference this account. Reassign ownership first, then try again."
                ),
                status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("Failed deleting user %s", user_id)
            return api_response(
                False,
                (
                    f"Cannot delete {user_label} right now because this account is still used by other records."
                ),
                status.HTTP_400_BAD_REQUEST,
            )

        return api_response(
            True,
            "User deleted successfully.",
            status.HTTP_200_OK,
            {"user_id": user_id},
        )

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["get"], url_path="supervisors")
    def supervisors(self, request):
        users = (
            User.objects.filter(
                profile__role__in=[UserProfile.Roles.ADMIN, UserProfile.Roles.BA],
                profile__status=UserProfile.Status.ACTIVE,
                is_superuser=False,
                is_staff=False,
            )
            .select_related("profile")
            .order_by("first_name", "last_name", "id")
        )
        page, paginator = paginate_request(request, users)
        targets = page if page is not None else users
        payload = [
            {
                "id": user.id,
                "name": user.get_full_name().strip() or user.email,
                "role": user.profile.role,
            }
            for user in targets
        ]
        if paginator is not None:
            return paginator.get_paginated_response(payload, message="Supervisors fetched.")
        return unpaginated_list_response(payload, message="Supervisors fetched.")




#project view set pagination
@extend_schema(tags=["BA/Admin APIs"])
class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all().order_by("-created_at")
    serializer_class = ProjectSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        queryset = Project.objects.annotate(files_attachment_count=Count("files", distinct=True)).order_by(
            "-created_at"
        )
        if user_role(self.request.user) == UserProfile.Roles.EMPLOYEE:
            return queryset.filter(tasks__assigned_to=self.request.user).distinct()
        return queryset

    def get_permissions(self):
        if self.action == "destroy":
            return [IsAuthenticated(), IsAdmin()]
        if self.action in {"create", "update", "partial_update", "request_deadline_change"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        if self.action in {
            "approve_deadline_change",
            "reject_deadline_change",
            "get_deadline_change_request",
        }:
            return [IsAuthenticated(), IsAdmin()]
        if self.action == "request_delete":
            return [IsAuthenticated(), IsAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        targets = page if page is not None else queryset
        for project in targets:
            sync_parent_statuses_for_project(project.id)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(request=DeadlineChangeRequestSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="request-deadline-change")
    def request_deadline_change(self, request, pk=None):
        project = self.get_object()
        if user_role(request.user) != UserProfile.Roles.BA:
            return api_response(False, "Only BA can request project deadline change.", status.HTTP_403_FORBIDDEN)

        serializer = DeadlineChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_deadline = serializer.validated_data.get("new_deadline")
        reason = serializer.validated_data.get("reason", "")

        if not new_deadline:
            return api_response(False, "new_deadline is required.", status.HTTP_400_BAD_REQUEST)
        if new_deadline < project.start_date:
            return api_response(
                False,
                "Requested deadline cannot be before the project start date.",
                status.HTTP_400_BAD_REQUEST,
            )

        if _get_pending_project_deadline_request(project):
            return api_response(
                False,
                "A deadline change request is already pending for this project.",
                status.HTTP_400_BAD_REQUEST,
            )

        change_request = ProjectDeadlineChangeRequest.objects.create(
            project=project,
            requested_by=request.user,
            current_deadline=project.deadline,
            requested_deadline=new_deadline,
            reason=reason or "",
        )

        admin_users = User.objects.filter(
            profile__role=UserProfile.Roles.ADMIN,
            profile__status=UserProfile.Status.ACTIVE,
        )
        admin_emails = _admin_mail_recipients()
        requester_name = _user_display_name(request.user)
        for admin_user in admin_users:
            Notification.objects.create(
                user=admin_user,
                type="PROJECT_DEADLINE_CHANGE_REQUEST",
                title="Project deadline change requested",
                message=_deadline_change_notification_message(
                    kind="project",
                    item_name=project.name,
                    requester=request.user,
                    old_deadline=project.deadline,
                    new_deadline=new_deadline,
                    reason=reason,
                ),
                details=_deadline_change_details_json(
                    project.deadline,
                    new_deadline,
                    request_id=change_request.id,
                    project_id=project.id,
                    project_name=project.name,
                    requester_name=requester_name,
                    reason=(reason or "").strip(),
                ),
                ref_type=Notification.RefType.PROJECT,
                ref_id=project.id,
            )

        send_project_change_request_email(
            project=project,
            requester=request.user,
            admin_emails=admin_emails,
            request_type="DEADLINE_CHANGE",
            new_deadline=new_deadline,
            reason=reason,
        )
        mail_triggered = bool(admin_emails)
        return api_response(
            True,
            "Project deadline change request sent to admin." + (" Mail sent successfully." if mail_triggered else ""),
            status.HTTP_200_OK,
            {
                "project_id": project.id,
                "request_id": change_request.id,
                "new_deadline": new_deadline,
                "reason": reason,
                "mail_triggered": mail_triggered,
            },
        )

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["get"], url_path="deadline-change-request")
    def get_deadline_change_request(self, request, pk=None):
        project = self.get_object()
        request_id = request.query_params.get("request_id")
        qs = ProjectDeadlineChangeRequest.objects.filter(project=project).select_related(
            "requested_by", "reviewed_by", "project"
        )
        if request_id:
            change_request = qs.filter(id=request_id).first()
        else:
            change_request = (
                qs.filter(status=ProjectDeadlineChangeRequest.Status.PENDING)
                .order_by("-created_at")
                .first()
            )
            if not change_request:
                change_request = qs.order_by("-created_at").first()
        if not change_request:
            return api_response(
                False,
                "No deadline change request found for this project.",
                status.HTTP_404_NOT_FOUND,
            )
        return api_response(
            True,
            "Deadline change request loaded.",
            status.HTTP_200_OK,
            _project_deadline_request_payload(change_request),
        )

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="approve-deadline-change")
    def approve_deadline_change(self, request, pk=None):
        project = self.get_object()
        change_request, error_response = _complete_project_deadline_review(
            project,
            request.user,
            approved=True,
        )
        if error_response:
            return error_response

        reviewer_name = _user_display_name(request.user)
        return api_response(
            True,
            f"Project deadline updated to {change_request.requested_deadline.isoformat()}.",
            status.HTTP_200_OK,
            {
                "project_id": project.id,
                "request_id": change_request.id,
                "new_deadline": change_request.requested_deadline,
                "reviewed_by": reviewer_name,
                **_project_deadline_request_payload(change_request),
            },
        )

    @extend_schema(request=DeadlineChangeRejectSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="reject-deadline-change")
    def reject_deadline_change(self, request, pk=None):
        project = self.get_object()
        serializer = DeadlineChangeRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rejection_reason = serializer.validated_data.get("reason", "")

        change_request, error_response = _complete_project_deadline_review(
            project,
            request.user,
            approved=False,
            rejection_reason=rejection_reason,
        )
        if error_response:
            return error_response

        reviewer_name = _user_display_name(request.user)
        return api_response(
            True,
            "Project deadline change request rejected.",
            status.HTTP_200_OK,
            {
                "project_id": project.id,
                "request_id": change_request.id,
                "reviewed_by": reviewer_name,
                "rejection_reason": rejection_reason or "",
                **_project_deadline_request_payload(change_request),
            },
        )

    @extend_schema(request=DeleteRequestSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="request-delete")
    def request_delete(self, request, pk=None):
        project = self.get_object()
        if user_role(request.user) != UserProfile.Roles.ADMIN:
            return api_response(False, "Only Admin can perform project delete action.", status.HTTP_403_FORBIDDEN)

        serializer = DeleteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")

        admin_users = User.objects.filter(
            profile__role=UserProfile.Roles.ADMIN,
            profile__status=UserProfile.Status.ACTIVE,
        )
        admin_emails = _admin_mail_recipients()
        for admin_user in admin_users:
            Notification.objects.create(
                user=admin_user,
                type="PROJECT_DELETE_REQUEST",
                title="Project delete requested",
                message=f"BA requested delete for project '{project.name}'.",
                ref_type=Notification.RefType.PROJECT,
                ref_id=project.id,
            )

        send_project_change_request_email(
            project=project,
            requester=request.user,
            admin_emails=admin_emails,
            request_type="DELETE",
            reason=reason,
        )
        mail_triggered = bool(admin_emails)
        return api_response(
            True,
            "Project delete request sent to admin." + (" Mail sent successfully." if mail_triggered else ""),
            status.HTTP_200_OK,
            {"project_id": project.id, "reason": reason, "mail_triggered": mail_triggered},
        )

    @extend_schema(
        summary="Project work-tracking progress",
        description=(
            "Returns progress_percent as a planned-hours-weighted average of task progress. "
            "Per-task: NOT_STARTED 0%, COMPLETED 100%, otherwise min(worked_hours/planned_hours×100, 95%). "
            "Planned hours = estimated_hours if set, else weekday count from created date to deadline × 8. "
            "Worked hours = tracked time (including active timer). "
            "progress_percent is null when no task has derivable planned hours."
        ),
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["get"], url_path="progress")
    def progress(self, request, pk=None):
        project = self.get_object()
        apply_automatic_task_status_rules()
        payload = project_progress_data(project)
        return api_response(True, "Project progress calculated.", status.HTTP_200_OK, payload)





#milestone view set pagination
@extend_schema(tags=["BA/Admin APIs"])
class MilestoneViewSet(viewsets.ModelViewSet):
    queryset = Milestone.objects.select_related("project").all().order_by("-created_at")
    serializer_class = MilestoneSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        if user_role(self.request.user) == UserProfile.Roles.EMPLOYEE:
            return queryset.filter(tasks__assigned_to=self.request.user).distinct()
        return queryset

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        targets = page if page is not None else queryset
        project_ids = {m.project_id for m in targets if m.project_id}
        for project_id in project_ids:
            sync_parent_statuses_for_project(project_id)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(
        summary="Milestone work-tracking progress",
        description=(
            "Same formula as project progress, scoped to tasks linked to this milestone. "
            "progress_percent is null when no task in the milestone has derivable planned hours."
        ),
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["get"], url_path="progress")
    def progress(self, request, pk=None):
        milestone = self.get_object()
        apply_automatic_task_status_rules()
        payload = milestone_progress_data(milestone)
        return api_response(True, "Milestone progress calculated.", status.HTTP_200_OK, payload)





#task view set pagination
@extend_schema(tags=["BA/Employee APIs"])
class TaskViewSet(viewsets.ModelViewSet):
    queryset = (
        Task.objects.select_related("project", "milestone", "assigned_to", "supervisor", "created_by")
        .all()
        .order_by("-created_at")
    )
    serializer_class = TaskSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    # JSON for actions like request-deadline-change, status PATCH; multipart for task document uploads.
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        apply_automatic_task_status_rules()
        user = self.request.user
        queryset = super().get_queryset()
        if not user.is_authenticated:
            return queryset.none()
        if user_role(user) == UserProfile.Roles.EMPLOYEE:
            return queryset.filter(assigned_to=user)
        return queryset

    def get_permissions(self):
        if self.action in {"create", "request_deadline_change", "partial_update"}:
            return [IsAuthenticated()]
        if self.action in {"update", "destroy", "assign"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        return [IsAuthenticated()]

    def _allowed_assignee_roles(self):
        actor_role = user_role(self.request.user)
        if actor_role == UserProfile.Roles.ADMIN:
            return {UserProfile.Roles.ADMIN, UserProfile.Roles.BA, UserProfile.Roles.EMPLOYEE}
        if actor_role == UserProfile.Roles.BA:
            return {UserProfile.Roles.ADMIN, UserProfile.Roles.EMPLOYEE}
        return set()

    def _validate_assignee(self, assignee):
        if assignee is None:
            return
        assignee_role = user_role(assignee)
        if assignee_role not in self._allowed_assignee_roles():
            raise PermissionDenied("You are not allowed to assign this task to the selected user.")
        assignee_profile = getattr(assignee, "profile", None)
        if not assignee_profile or assignee_profile.status != UserProfile.Status.ACTIVE:
            raise ValidationError({"assigned_to": "Assigned user must be active."})

    def _can_manage_assigned_task_timer(self, user, task):
        """Assigned Employee, Admin, or BA may start/pause/stop their own task timer."""
        if task.assigned_to_id != user.id:
            return False
        return user_role(user) in {
            UserProfile.Roles.EMPLOYEE,
            UserProfile.Roles.ADMIN,
            UserProfile.Roles.BA,
        }

    def _validate_project_milestone_scope(self, project, milestone):
        actor = self.request.user
        actor_role = user_role(actor)

        if not project:
            if milestone:
                raise ValidationError({"project": "Project is required when a milestone is selected."})
            return

        # Always ensure milestone belongs to the selected project.
        if milestone and milestone.project_id != project.id:
            raise ValidationError({"milestone": "Selected milestone does not belong to the selected project."})

        # BA can work only on Admin-created or self-created project/milestone scope.
        if actor_role == UserProfile.Roles.BA:
            allowed_creator_ids = [actor.id]
            admin_ids = list(
                User.objects.filter(
                    profile__role=UserProfile.Roles.ADMIN,
                    profile__status=UserProfile.Status.ACTIVE,
                ).values_list("id", flat=True)
            )
            allowed_creator_ids.extend(admin_ids)

            if project.created_by_id not in allowed_creator_ids:
                raise PermissionDenied("BA can create tasks only in Admin-created or own projects.")
            if milestone and milestone.created_by_id not in allowed_creator_ids:
                raise PermissionDenied("BA can use milestones created by Admin or by self only.")

    def _reset_mail_state(self):
        self._mail_triggered = False

    def _perform_employee_self_create(self, serializer):
        if user_role(self.request.user) != UserProfile.Roles.EMPLOYEE:
            raise PermissionDenied("Only employees can self-create tasks.")

        self._reset_mail_state()
        project = serializer.validated_data.get("project")
        milestone = serializer.validated_data.get("milestone")
        supervisor = serializer.validated_data.get("supervisor")
        self._validate_project_milestone_scope(project, milestone)

        if not supervisor:
            raise ValidationError(
                {"supervisor": "Select an Admin or BA to notify about this task."}
            )

        task = serializer.save(
            created_by=self.request.user,
            assigned_to=self.request.user,
            is_self_created=True,
            supervisor=supervisor,
        )
        _sync_parent_statuses_for_task(task)

        employee_name = self.request.user.get_full_name().strip() or self.request.user.email
        notify_user = task.supervisor or supervisor
        Notification.objects.create(
            user=notify_user,
            type="TASK_SELF_CREATED",
            title="Employee self-created task",
            message=(
                f"{employee_name} created task '{task.title}'"
                + (f" under project '{task.project.name}'." if task.project_id else ".")
            ),
            ref_type=Notification.RefType.TASK,
            ref_id=task.id,
            details={
                "employee_id": self.request.user.id,
                "employee_name": employee_name,
                "supervisor_id": notify_user.id,
                "project_id": task.project_id,
                "milestone_id": task.milestone_id,
                "deadline": task.deadline.isoformat() if task.deadline else None,
                "is_self_created": True,
            },
        )
        send_task_self_created_email(task, self.request.user, notify_user)
        self._mail_triggered = True

    def perform_create(self, serializer):
        if user_role(self.request.user) == UserProfile.Roles.EMPLOYEE:
            self._perform_employee_self_create(serializer)
            return

        self._reset_mail_state()
        project = serializer.validated_data.get("project")
        milestone = serializer.validated_data.get("milestone")
        actor_role = user_role(self.request.user)
        if not project and actor_role == UserProfile.Roles.ADMIN:
            raise ValidationError({"project": "Project is required."})
        self._validate_project_milestone_scope(project, milestone)
        self._validate_assignee(serializer.validated_data.get("assigned_to"))
        task = serializer.save(created_by=self.request.user)
        _sync_parent_statuses_for_task(task)
        if task.assigned_to and task.assigned_to.email:
            Notification.objects.create(
                user=task.assigned_to,
                type="TASK_ASSIGNED",
                title="New Task Assigned",
                message=f"Task '{task.title}' was assigned to you.",
                ref_type=Notification.RefType.TASK,
                ref_id=task.id,
            )
            send_task_assignment_email(task.assigned_to, task)
            self._mail_triggered = True

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        payload = dict(serializer.data)
        payload["mail_triggered"] = bool(getattr(self, "_mail_triggered", False))
        msg = "Task created successfully."
        if payload["mail_triggered"]:
            msg = "Task created successfully. Mail sent successfully."
        return api_response(True, msg, status.HTTP_201_CREATED, payload)

    def _employee_allowed_patch_fields(self, task):
        allowed = {"title", "description"}
        if task.is_self_created:
            allowed.update({"project", "project_name", "milestone", "milestone_name"})
        return allowed

    def _employee_patch_payload(self, request, allowed_fields):
        if hasattr(request.data, "lists"):
            raw = {key: request.data.get(key) for key in request.data}
        else:
            raw = dict(request.data)
        return {key: value for key, value in raw.items() if key in allowed_fields}

    def perform_employee_update(self, serializer):
        instance = serializer.instance
        if instance.is_self_created:
            project = serializer.validated_data.get("project", instance.project)
            milestone = serializer.validated_data.get("milestone", instance.milestone)
            self._validate_project_milestone_scope(project, milestone)
        task = serializer.save()
        _sync_parent_statuses_for_task(task)

    def _employee_partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.assigned_to_id != request.user.id:
            raise PermissionDenied("You can only edit tasks assigned to you.")

        allowed_fields = self._employee_allowed_patch_fields(instance)
        filtered = self._employee_patch_payload(request, allowed_fields)
        if not filtered:
            return api_response(
                False,
                "No editable fields provided.",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(instance, data=filtered, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_employee_update(serializer)
        return api_response(
            True,
            "Task updated successfully.",
            status.HTTP_200_OK,
            dict(serializer.data),
        )

    def perform_update(self, serializer):
        self._reset_mail_state()
        previous_assignee_id = getattr(serializer.instance, "assigned_to_id", None)
        previous_deadline = getattr(serializer.instance, "deadline", None)
        project = serializer.validated_data.get("project", serializer.instance.project)
        milestone = serializer.validated_data.get("milestone", serializer.instance.milestone)
        self._validate_project_milestone_scope(project, milestone)
        assignee = serializer.validated_data.get("assigned_to", serializer.instance.assigned_to)
        self._validate_assignee(assignee)
        requested_status = serializer.validated_data.get("status", serializer.instance.status)
        if requested_status == Task.Status.COMPLETED:
            _stop_timers_before_complete(serializer.instance, self.request.user)
        task = serializer.save()
        _sync_parent_statuses_for_task(task)
        if assignee and assignee.email and assignee.id != previous_assignee_id:
            Notification.objects.create(
                user=assignee,
                type="TASK_ASSIGNED",
                title="Task Assigned/Updated",
                message=f"Task '{task.title}' is assigned to you.",
                ref_type=Notification.RefType.TASK,
                ref_id=task.id,
            )
            send_task_assignment_email(assignee, task)
            self._mail_triggered = True

        # When BA/Admin updates a requested deadline, notify the assigned employee.
        new_deadline = task.deadline
        actor_role = user_role(self.request.user)
        if (
            assignee
            and previous_deadline != new_deadline
            and actor_role in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}
        ):
            Notification.objects.create(
                user=assignee,
                type="TASK_DEADLINE_UPDATED",
                title="Task deadline updated",
                message=(
                    f"Your deadline request for task '{task.title}' was updated to "
                    f"{new_deadline.isoformat() if new_deadline else 'Not set'} by "
                    f"{'Admin' if actor_role == UserProfile.Roles.ADMIN else 'BA'}."
                ),
                details=_deadline_change_details_json(previous_deadline, new_deadline),
                ref_type=Notification.RefType.TASK,
                ref_id=task.id,
            )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        payload = dict(serializer.data)
        payload["mail_triggered"] = bool(getattr(self, "_mail_triggered", False))
        msg = "Task updated successfully."
        if payload["mail_triggered"]:
            msg = "Task updated successfully. Mail sent successfully."
        return api_response(True, msg, status.HTTP_200_OK, payload)

    def partial_update(self, request, *args, **kwargs):
        if user_role(request.user) == UserProfile.Roles.EMPLOYEE:
            return self._employee_partial_update(request, *args, **kwargs)
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        task = self.get_object()
        task_id = task.id
        self.perform_destroy(task)
        _sync_parent_statuses_for_task(task)
        return api_response(
            True,
            "Task deleted successfully.",
            status.HTTP_200_OK,
            {"task_id": task_id},
        )

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        task = self.get_object()
        user_id = request.data.get("user_id")
        allowed_roles = self._allowed_assignee_roles()
        assignee = User.objects.filter(
            id=user_id, profile__role__in=allowed_roles, profile__status=UserProfile.Status.ACTIVE
        ).first()
        if not assignee:
            return api_response(False, "Assignee not found or not allowed.", status.HTTP_400_BAD_REQUEST)
        task.assigned_to = assignee
        task.save(update_fields=["assigned_to"])
        Notification.objects.create(
            user=assignee,
            type="TASK_ASSIGNED",
            title="New Task Assigned",
            message=f"Task '{task.title}' was assigned to you.",
            ref_type=Notification.RefType.TASK,
            ref_id=task.id,
        )
        send_task_assignment_email(assignee, task)
        return api_response(
            True,
            "Task assigned successfully. Mail sent successfully.",
            status.HTTP_200_OK,
            {
                "task_id": task.id,
                "task_name": task.title,
                "assigned_id": assignee.id,
                "emp_name": assignee.first_name,
                "mail_triggered": True,
            },
        )

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        task = self.get_object()
        if not self._can_manage_assigned_task_timer(request.user, task):
            return api_response(
                False,
                "Only the assigned user can start this task.",
                status.HTTP_403_FORBIDDEN,
            )
        if TimeLog.objects.filter(user=request.user, end_time__isnull=True).exists():
            return api_response(False, "You already have an active timer.", status.HTTP_400_BAD_REQUEST)
        now = timezone.now()
        TimeLog.objects.create(
            task=task,
            user=request.user,
            start_time=now,
            last_activity_at=now,
        )
        task.status = Task.Status.IN_PROGRESS
        task.save(update_fields=["status"])
        _sync_parent_statuses_for_task(task)
        return api_response(True, "Task started.", status.HTTP_200_OK, {"task_id": task.id, "status": task.status})

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        task = self.get_object()
        if not self._can_manage_assigned_task_timer(request.user, task):
            return api_response(
                False,
                "Only the assigned user can pause this task.",
                status.HTTP_403_FORBIDDEN,
            )
        log = TimeLog.objects.filter(task=task, user=request.user, end_time__isnull=True).last()
        if not log:
            return api_response(False, "No active timer found for this task.", status.HTTP_400_BAD_REQUEST)
        log.stop(source=TimeLog.Source.MANUAL_PAUSE)
        task.status = Task.Status.PAUSED
        # Do not save total_time_spent_seconds here: log.stop() already persisted it on Task;
        # this `task` instance is stale and would overwrite the correct total with 0/old value.
        task.save(update_fields=["status"])
        _sync_parent_statuses_for_task(task)
        return api_response(True, "Task paused.", status.HTTP_200_OK, {"task_id": task.id, "status": task.status})

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        task = self.get_object()
        if not self._can_manage_assigned_task_timer(request.user, task):
            return api_response(
                False,
                "Only the assigned user can stop this task.",
                status.HTTP_403_FORBIDDEN,
            )
        log = TimeLog.objects.filter(task=task, user=request.user, end_time__isnull=True).last()
        if not log:
            return api_response(False, "No active timer found for this task.", status.HTTP_400_BAD_REQUEST)
        log.stop(source=TimeLog.Source.MANUAL_STOP)
        if task.status != Task.Status.COMPLETED:
            task.status = Task.Status.PAUSED
            task.save(update_fields=["status"])
        _sync_parent_statuses_for_task(task)
        if user_role(request.user) == UserProfile.Roles.EMPLOYEE:
            return api_response(
                True,
                "Task stopped.",
                status.HTTP_200_OK,
                {"task_id": task.id, "status": task.status},
            )
        task.refresh_from_db(fields=["total_time_spent_seconds"])
        return api_response(
            True,
            "Task stopped.",
            status.HTTP_200_OK,
            {
                "task_id": task.id,
                "end_time": log.end_time,
                "duration_seconds": log.duration_seconds,
                "duration_display": humanize_duration(log.duration_seconds),
                "total_time_spent_seconds": task.total_time_spent_seconds,
                "total_time_spent_display": humanize_duration(task.total_time_spent_seconds),
            },
        )

    @action(detail=True, methods=["get"], url_path="time-logs")
    def time_logs(self, request, pk=None):
        if user_role(request.user) not in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}:
            return api_response(False, "Only Admin or BA can view time logs.", status.HTTP_403_FORBIDDEN)
        task = self.get_object()
        logs = assignee_time_logs_queryset(
            task,
            user_id=request.query_params.get("user_id"),
        )
        logs = logs.order_by("-start_time")
        history = logs.aggregate(
            start_count=Count("id"),
            pause_count=Count("id", filter=Q(source=TimeLog.Source.MANUAL_PAUSE)),
            stop_count=Count("id", filter=Q(source=TimeLog.Source.MANUAL_STOP)),
            auto_stop_count=Count("id", filter=Q(source=TimeLog.Source.AUTO_STOP_8PM)),
        )
        page, paginator = paginate_request(request, logs)
        targets = page if page is not None else logs
        serialized_logs = TimeLogSerializer(targets, many=True).data
        if paginator is not None:
            return Response(
                {
                    "success": True,
                    "message": "Time logs fetched.",
                    "code": status.HTTP_200_OK,
                    "data": {
                        "history": history,
                        "time_logs": serialized_logs,
                    },
                    "meta": paginator._meta_payload(),
                }
            )
        return api_response(
            True,
            "Time logs fetched.",
            status.HTTP_200_OK,
            {
                "history": history,
                "time_logs": serialized_logs,
            },
        )

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        task = self.get_object()
        status_value = request.data.get("status")
        if status_value not in Task.Status.values:
            return api_response(False, "Invalid status.", status.HTTP_400_BAD_REQUEST)
        if status_value == Task.Status.COMPLETED:
            _stop_timers_before_complete(task, request.user)
        task.status = status_value
        task.save(update_fields=["status"])
        _sync_parent_statuses_for_task(task)
        mail_triggered = False
        if status_value == Task.Status.COMPLETED:
            recipient = _task_completion_recipient(task)
            if recipient:
                if task.is_self_created and task.supervisor_id:
                    employee_name = (
                        request.user.get_full_name().strip() or request.user.email
                    )
                    completion_message = (
                        f"{employee_name} completed self-created task '{task.title}'."
                    )
                else:
                    completion_message = f"Task '{task.title}' has been completed."
                Notification.objects.create(
                    user=recipient,
                    type="TASK_COMPLETED",
                    title="Task Completed",
                    message=completion_message,
                    ref_type=Notification.RefType.TASK,
                    ref_id=task.id,
                )
                send_task_completed_email(task, request.user)
                mail_triggered = True
        return api_response(
            True,
            "Task status updated." + (" Mail sent successfully." if mail_triggered else ""),
            status.HTTP_200_OK,
            {
                "task_id": task.id,
                "task_name": task.title,
                "status": task.status,
                "mail_triggered": mail_triggered,
            },
        )

    @extend_schema(request=DeadlineChangeRequestSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="request-deadline-change")
    def request_deadline_change(self, request, pk=None):
        task = self.get_object()
        if user_role(request.user) != UserProfile.Roles.EMPLOYEE or task.assigned_to_id != request.user.id:
            return api_response(
                False,
                "Only assigned employee can request task deadline change.",
                status.HTTP_403_FORBIDDEN,
            )

        serializer = DeadlineChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_deadline = serializer.validated_data.get("new_deadline")
        reason = serializer.validated_data.get("reason", "")
        owner = _task_deadline_change_recipient(task)
        mail_triggered = False

        if owner:
            Notification.objects.create(
                user=owner,
                type="TASK_DEADLINE_CHANGE_REQUEST",
                title="Task deadline change requested",
                message=_deadline_change_notification_message(
                    kind="task",
                    item_name=task.title,
                    requester=request.user,
                    old_deadline=task.deadline,
                    new_deadline=new_deadline,
                    reason=reason,
                ),
                details=_deadline_change_details_json(task.deadline, new_deadline),
                ref_type=Notification.RefType.TASK,
                ref_id=task.id,
            )
            send_task_deadline_change_request_email(
                task=task,
                employee=request.user,
                owner=owner,
                new_deadline=new_deadline,
                reason=reason,
            )
            mail_triggered = True

        return api_response(
            True,
            "Task deadline change request sent." + (" Mail sent successfully." if mail_triggered else ""),
            status.HTTP_200_OK,
            {
                "task_id": task.id,
                "new_deadline": new_deadline,
                "reason": reason,
                "mail_triggered": mail_triggered,
            },
        )





#file attachment view set pagination
@extend_schema(tags=["Common APIs"])
class FileAttachmentViewSet(viewsets.ModelViewSet):
    queryset = (
        FileAttachment.objects.select_related(
            "uploaded_by",
            "project",
            "milestone",
            "milestone__project",
            "task",
            "task__project",
        )
        .all()
        .order_by("-created_at")
    )
    serializer_class = FileAttachmentSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        return [IsAuthenticated()]

    def get_queryset(self):
        base_qs = (
            FileAttachment.objects.select_related(
                "uploaded_by",
                "project",
                "milestone",
                "milestone__project",
                "task",
                "task__project",
            )
            .all()
            .order_by("-created_at")
        )
        role = user_role(self.request.user)
        if role == UserProfile.Roles.EMPLOYEE:
            # Employee can view docs linked to their assigned tasks,
            # including project-level and milestone-level docs in the same scope.
            qs = base_qs.filter(
                Q(task__assigned_to=self.request.user)
                | Q(milestone__tasks__assigned_to=self.request.user)
                | Q(project__tasks__assigned_to=self.request.user)
            ).distinct()
        else:
            qs = base_qs

        def _parse_id(key: str) -> int | None:
            raw = self.request.query_params.get(key)
            if raw in (None, ""):
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None

        project_id = _parse_id("project")
        if project_id is not None:
            qs = qs.filter(project_id=project_id)
        milestone_id = _parse_id("milestone")
        if milestone_id is not None:
            qs = qs.filter(milestone_id=milestone_id)
        task_id = _parse_id("task")
        if task_id is not None:
            qs = qs.filter(task_id=task_id)
        return qs

    @extend_schema(request=FileUploadRequestSerializer, responses={201: FileAttachmentSerializer})
    def create(self, request, *args, **kwargs):
        request_serializer = FileUploadRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        payload = request_serializer.validated_data
        model_serializer = self.get_serializer(data=payload)
        model_serializer.is_valid(raise_exception=True)
        self.perform_create(model_serializer)
        return api_response(True, "File uploaded successfully.", status.HTTP_201_CREATED, model_serializer.data)

    def perform_create(self, serializer):
        upload = self.request.FILES.get("file")
        serializer.save(
            uploaded_by=self.request.user,
            mime_type=getattr(upload, "content_type", ""),
            size_bytes=getattr(upload, "size", 0),
        )





@extend_schema(tags=["Common APIs"])
class InternalAdminUsersAPIView(APIView):
    """Service-to-service: list active admin users for leave alerts."""

    authentication_classes = []
    permission_classes = [IsServiceToken]

    def get(self, request):
        fixed_admin_emails = _admin_mail_recipients()
        users_by_email = {
            (user.email or "").lower(): user
            for user in User.objects.filter(email__in=fixed_admin_emails).select_related("profile")
        }
        payload = []
        for email in fixed_admin_emails:
            user = users_by_email.get(email.lower())
            payload.append(
                {
                    "id": user.id if user else None,
                    "email": email,
                    "first_name": user.first_name if user else "",
                    "last_name": user.last_name if user else "",
                    "role": UserProfile.Roles.ADMIN,
                    "status": UserProfile.Status.ACTIVE,
                }
            )
        page, paginator = paginate_request(request, payload)
        paged_payload = page if page is not None else payload
        if paginator is not None:
            return paginator.get_paginated_response(
                paged_payload,
                message="Admin users fetched.",
            )
        return unpaginated_list_response(paged_payload, message="Admin users fetched.")


@extend_schema(tags=["Common APIs"])
class InternalUserDetailAPIView(APIView):
    """Service-to-service: user email/name for attendance emails."""

    authentication_classes = []
    permission_classes = [IsServiceToken]

    def get(self, request, user_id):
        user = User.objects.filter(pk=user_id).select_related("profile").first()
        if not user:
            return api_response(False, "User not found.", status.HTTP_404_NOT_FOUND)
        profile = getattr(user, "profile", None)
        return api_response(
            True,
            "User fetched.",
            status.HTTP_200_OK,
            {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": getattr(profile, "role", "") or "",
                "status": getattr(profile, "status", "") or "",
                "department": getattr(profile, "department", "") or "",
            },
        )


@extend_schema(tags=["Common APIs"])
class InternalStaffUsersAPIView(APIView):
    """Service-to-service: active Employee/BA users for attendance name resolution."""

    authentication_classes = []
    permission_classes = [IsServiceToken]

    def get(self, request):
        staff = (
            User.objects.filter(
                profile__role__in=[UserProfile.Roles.EMPLOYEE, UserProfile.Roles.BA],
                profile__status=UserProfile.Status.ACTIVE,
            )
            .select_related("profile")
            .order_by("id")
        )
        page, paginator = paginate_request(request, staff)
        targets = page if page is not None else staff
        payload = [
            {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": user.profile.role,
                "status": user.profile.status,
                "department": user.profile.department or "",
            }
            for user in targets
        ]
        if paginator is not None:
            return paginator.get_paginated_response(
                payload,
                message="Staff users fetched.",
            )
        return unpaginated_list_response(payload, message="Staff users fetched.")


@extend_schema(tags=["Common APIs"])
class InternalNotificationCreateAPIView(APIView):
    """Service-to-service: create in-app notifications (attendance leave flow)."""

    authentication_classes = []
    permission_classes = [IsServiceToken]

    @extend_schema(
        request=InternalNotificationCreateSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        serializer = InternalNotificationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        created_ids = []
        for item in serializer.validated_data["notifications"]:
            user = User.objects.filter(pk=item["user_id"]).first()
            if not user:
                continue
            ref_type = (item.get("ref_type") or "").strip()
            notification = Notification.objects.create(
                user=user,
                type=item["type"],
                title=item["title"],
                message=item["message"],
                ref_type=ref_type,
                ref_id=item.get("ref_id"),
                details=item.get("details"),
            )
            created_ids.append(notification.id)
        return api_response(
            True,
            f"{len(created_ids)} notification(s) created.",
            status.HTTP_201_CREATED,
            {"created_ids": created_ids},
        )


#notification view set pagination
@extend_schema(tags=["Common APIs"])
class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user).order_by("-created_at")

    @action(detail=True, methods=["patch"])
    def read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        return api_response(True, "Notification marked as read.", status.HTTP_200_OK, {"id": notification.id})

    @action(detail=False, methods=["post"], url_path="clear")
    def clear(self, request):
        deleted_count, _ = self.get_queryset().delete()
        return api_response(
            True,
            "Notifications cleared successfully.",
            status.HTTP_200_OK,
            {"deleted_count": deleted_count},
        )


#dashboard api view
@extend_schema(tags=["Common APIs"])
class DashboardAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        user = request.user
        if user_role(user) == UserProfile.Roles.ADMIN:
            data = build_admin_overview_payload()
            return api_response(True, "Admin dashboard fetched (same as admin overview).", status.HTTP_200_OK, data)
        if user_role(user) == UserProfile.Roles.BA:
            allowed_creator_ids = [user.id]
            admin_ids = list(
                User.objects.filter(
                    profile__role=UserProfile.Roles.ADMIN,
                    profile__status=UserProfile.Status.ACTIVE,
                ).values_list("id", flat=True)
            )
            allowed_creator_ids.extend(admin_ids)

            # BA dashboard: tasks they created + employee self-created tasks they supervise.
            ba_tasks_qs = Task.objects.filter(
                Q(created_by_id__in=allowed_creator_ids) | Q(supervisor=user)
            ).select_related("project", "milestone", "assigned_to", "supervisor")
            employee_ids = list(ba_tasks_qs.exclude(assigned_to_id__isnull=True).values_list("assigned_to_id", flat=True).distinct())
            employee_rows = list(
                User.objects.filter(id__in=employee_ids, profile__role=UserProfile.Roles.EMPLOYEE)
                .annotate(
                    assigned_tasks_count=Count(
                        "tasks_assigned", filter=Q(tasks_assigned__created_by=user), distinct=True
                    ),
                    completed_tasks_count=Count(
                        "tasks_assigned",
                        filter=Q(tasks_assigned__created_by=user, tasks_assigned__status=Task.Status.COMPLETED),
                        distinct=True,
                    ),
                    in_progress_tasks_count=Count(
                        "tasks_assigned",
                        filter=Q(tasks_assigned__created_by=user, tasks_assigned__status=Task.Status.IN_PROGRESS),
                        distinct=True,
                    ),
                    delayed_tasks_count=Count(
                        "tasks_assigned",
                        filter=Q(tasks_assigned__created_by=user, tasks_assigned__status=Task.Status.DELAYED),
                        distinct=True,
                    ),
                )
                .values(
                    "id",
                    "first_name",
                    "last_name",
                    "email",
                    "assigned_tasks_count",
                    "completed_tasks_count",
                    "in_progress_tasks_count",
                    "delayed_tasks_count",
                )
                .order_by("first_name", "id")
            )
            employee_summary = []
            for row in employee_rows:
                tasks_for_employee = ba_tasks_qs.filter(assigned_to_id=row["id"]).order_by("-updated_at")
                employee_summary.append(
                    {
                        "id": row["id"],
                        "first_name": row["first_name"],
                        "last_name": row["last_name"],
                        "email": row["email"],
                        "assigned_tasks": row["assigned_tasks_count"],
                        "completed_tasks": row["completed_tasks_count"],
                        "in_progress_tasks": row["in_progress_tasks_count"],
                        "delayed_tasks": row["delayed_tasks_count"],
                        "tasks": [
                            {
                                "id": task.id,
                                "title": task.title,
                                "status": task.status,
                                "project_id": task.project_id,
                                "project_name": _task_project_name(task),
                                "milestone_id": task.milestone_id,
                                "milestone_no": task.milestone.milestone_no if task.milestone else None,
                                "milestone_name": task.milestone.name if task.milestone else None,
                                "deadline": task.deadline,
                                "total_time_spent_seconds": task.total_time_spent_seconds,
                                "total_time_spent_display": humanize_duration(task.total_time_spent_seconds),
                            }
                            for task in tasks_for_employee
                        ],
                    }
                )
            task_ids = list(ba_tasks_qs.values_list("id", flat=True))
            project_ids = list(ba_tasks_qs.values_list("project_id", flat=True).distinct())
            milestone_ids = list(
                ba_tasks_qs.exclude(milestone_id__isnull=True).values_list("milestone_id", flat=True).distinct()
            )
            project_qs = Project.objects.filter(id__in=project_ids)
            milestone_qs = Milestone.objects.filter(id__in=milestone_ids)
            task_status_counts = {
                "not_started": ba_tasks_qs.filter(status=Task.Status.NOT_STARTED).count(),
                "in_progress": ba_tasks_qs.filter(status=Task.Status.IN_PROGRESS).count(),
                "paused": ba_tasks_qs.filter(status=Task.Status.PAUSED).count(),
                "completed": ba_tasks_qs.filter(status=Task.Status.COMPLETED).count(),
                "delayed": ba_tasks_qs.filter(status=Task.Status.DELAYED).count(),
                "blocked": ba_tasks_qs.filter(status=Task.Status.BLOCKED).count(),
            }
            logs_qs = (
                TimeLog.objects.select_related("task", "task__project", "user")
                .filter(task_id__in=task_ids)
                .order_by("-start_time")
            )
            recent_activity = []
            for log in logs_qs[:80]:
                user_name = log.user.get_full_name().strip() or log.user.email
                recent_activity.append(
                    {
                        "action": "STARTED",
                        "employee_name": user_name,
                        "task_id": log.task_id,
                        "task_title": log.task.title,
                        "project_name": _task_project_name(log.task),
                        "timestamp": log.start_time,
                    }
                )
                if log.end_time:
                    action = "PAUSED" if log.source == TimeLog.Source.MANUAL_PAUSE else "STOPPED"
                    recent_activity.append(
                        {
                            "action": action,
                            "employee_name": user_name,
                            "task_id": log.task_id,
                            "task_title": log.task.title,
                            "project_name": _task_project_name(log.task),
                            "timestamp": log.end_time,
                        }
                    )
            recent_activity.sort(key=lambda item: item["timestamp"], reverse=True)
            window_start = _recent_activity_window_start()
            notification_events = _build_notification_recent_activity(user, window_start)
            recent_activity = _merge_recent_activity_events(
                recent_activity,
                notification_events,
                limit=20,
            )
            data = {
                "tasks_created": ba_tasks_qs.count(),
                "tasks_completed": ba_tasks_qs.filter(status=Task.Status.COMPLETED).count(),
                "tasks_in_progress": ba_tasks_qs.filter(status=Task.Status.IN_PROGRESS).count(),
                "tasks_delayed": ba_tasks_qs.filter(status=Task.Status.DELAYED).count(),
                "assigned_employees": len(employee_ids),
                "overview": {
                    "projects_count": project_qs.count(),
                    "tasks_count": ba_tasks_qs.count(),
                    "employee_count": len(employee_ids),
                },
                "task_status_counts": task_status_counts,
                "projects": [
                    {
                        "id": project.id,
                        "name": project.name,
                        "status": project.status,
                        "start_date": project.start_date,
                        "deadline": project.deadline,
                    }
                    for project in project_qs
                ],
                "milestones": [
                    {
                        "id": milestone.id,
                        "milestone_no": milestone.milestone_no,
                        "name": milestone.name,
                        "project_id": milestone.project_id,
                        "status": milestone.status,
                        "start_date": milestone.start_date,
                        "end_date": milestone.end_date,
                    }
                    for milestone in milestone_qs
                ],
                "tasks": [
                    {
                        "id": task.id,
                        "title": task.title,
                        "project_id": task.project_id,
                        "project_name": _task_project_name(task),
                        "milestone_name": task.milestone.name if task.milestone else None,
                        "status": task.status,
                    }
                    for task in ba_tasks_qs.order_by("-updated_at")
                ],
                "recent_activity": recent_activity,
                "employee_summary": employee_summary,
            }
            return api_response(True, "BA dashboard fetched.", status.HTTP_200_OK, data)
        active_log = (
            TimeLog.objects.filter(user=user, end_time__isnull=True)
            .select_related("task")
            .order_by("-start_time")
            .first()
        )
        active_task = (
            {"id": active_log.task_id, "title": active_log.task.title}
            if active_log
            else None
        )
        data = {
            "active_task": active_task,
            "completed_tasks": visible_completed_tasks_for_user(user).count(),
            "work_history_retention_months": WORK_HISTORY_RETENTION_MONTHS,
        }
        return api_response(True, "Employee dashboard fetched.", status.HTTP_200_OK, data)





@extend_schema(tags=["BA/Admin APIs"])
class WorkTrackingAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        apply_automatic_task_status_rules()
        role = user_role(request.user)
        if role not in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA, UserProfile.Roles.EMPLOYEE}:
            return api_response(False, "Only Admin, BA, or Employee can access work tracking.", status.HTTP_403_FORBIDDEN)

        if role == UserProfile.Roles.EMPLOYEE:
            tasks_qs = (
                Task.objects.select_related("project", "milestone", "assigned_to", "created_by")
                .filter(assigned_to=request.user)
                .order_by("-updated_at")
            )
        else:
            tasks_qs = (
                Task.objects.select_related("project", "milestone", "assigned_to", "created_by")
                .filter(assigned_to__isnull=False)
                .order_by("-updated_at")
            )
        if role == UserProfile.Roles.BA:
            allowed_creator_ids = [request.user.id]
            admin_ids = list(
                User.objects.filter(
                    profile__role=UserProfile.Roles.ADMIN,
                    profile__status=UserProfile.Status.ACTIVE,
                ).values_list("id", flat=True)
            )
            allowed_creator_ids.extend(admin_ids)
            tasks_qs = tasks_qs.filter(
                Q(created_by_id__in=allowed_creator_ids) | Q(supervisor=request.user)
            )

        employee_id = request.query_params.get("employee_id")
        project_id = request.query_params.get("project_id")
        milestone_id = request.query_params.get("milestone_id")
        task_id = request.query_params.get("task_id")
        status_filter = request.query_params.get("status")
        only_active = request.query_params.get("only_active")
        date_from_raw = request.query_params.get("date_from")
        date_to_raw = request.query_params.get("date_to")

        range_start = range_end = None
        if date_from_raw or date_to_raw:
            if not date_from_raw or not date_to_raw:
                return api_response(
                    False,
                    "Both date_from and date_to are required for date filtering.",
                    status.HTTP_400_BAD_REQUEST,
                )
            try:
                range_start = datetime.strptime(date_from_raw, "%Y-%m-%d").date()
                range_end = datetime.strptime(date_to_raw, "%Y-%m-%d").date()
            except ValueError:
                return api_response(
                    False,
                    "date_from and date_to must be YYYY-MM-DD.",
                    status.HTTP_400_BAD_REQUEST,
                )
            if range_start > range_end:
                return api_response(
                    False,
                    "date_from must be on or before date_to.",
                    status.HTTP_400_BAD_REQUEST,
                )

        # Employees only ever see their own assignments; ignore employee_id for others' data.
        if employee_id and role in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}:
            tasks_qs = tasks_qs.filter(assigned_to_id=employee_id)
        if project_id:
            tasks_qs = tasks_qs.filter(project_id=project_id)
        if milestone_id:
            tasks_qs = tasks_qs.filter(milestone_id=milestone_id)
        if task_id:
            tasks_qs = tasks_qs.filter(id=task_id)
        if status_filter:
            tasks_qs = tasks_qs.filter(status=status_filter)

        now = timezone.now()
        today = timezone.localdate()
        records = []
        for task in tasks_qs:
            active_log = TimeLog.objects.filter(task=task, end_time__isnull=True).order_by("-start_time").first()
            timer_state = assignee_timer_state(task)
            # No TimeLogs yet: keep rows consistent for filters/summary (NOT_STARTED leaves null).
            if timer_state is None and task.status != Task.Status.NOT_STARTED:
                timer_state = "PAUSED" if task.status == Task.Status.PAUSED else "STOPPED"

            if only_active and only_active.lower() == "true" and timer_state != "STARTED":
                continue

            todays_logs = TimeLog.objects.filter(task=task, user=task.assigned_to, start_time__date=today)
            today_worked_seconds = sum(todays_logs.values_list("duration_seconds", flat=True))
            current_session_seconds = 0
            if active_log:
                if (
                    role == UserProfile.Roles.EMPLOYEE
                    and task.assigned_to_id == request.user.id
                ):
                    active_log.touch_last_activity(when=now)
                current_session_seconds = int((now - active_log.start_time).total_seconds())
                if active_log.start_time.date() == today:
                    today_worked_seconds += current_session_seconds

            if range_start and range_end:
                total_tracked_seconds = worked_seconds_in_range(
                    task, range_start, range_end, now=now
                )
                if total_tracked_seconds <= 0:
                    continue
            else:
                completed_duration_sum = (
                    TimeLog.objects.filter(
                        task=task,
                        user=task.assigned_to,
                        end_time__isnull=False,
                    ).aggregate(s=Sum("duration_seconds"))["s"]
                    or 0
                )
                total_tracked_seconds = int(completed_duration_sum) + int(
                    current_session_seconds
                )

            visible_logs = assignee_time_logs_queryset(task)
            last_completed_log = (
                visible_logs.filter(end_time__isnull=False).order_by("-end_time").first()
            )
            history = visible_logs.aggregate(
                start_count=Count("id"),
                pause_count=Count("id", filter=Q(source=TimeLog.Source.MANUAL_PAUSE)),
                stop_count=Count("id", filter=Q(source=TimeLog.Source.MANUAL_STOP)),
                auto_stop_count=Count("id", filter=Q(source=TimeLog.Source.AUTO_STOP_8PM)),
            )

            records.append(
                {
                    "employee_id": task.assigned_to_id,
                    "employee_name": task.assigned_to.get_full_name().strip() or task.assigned_to.email,
                    "employee_email": task.assigned_to.email,
                    "project_id": task.project_id,
                    "project_name": _task_project_name(task),
                    "milestone_id": task.milestone_id,
                    "milestone_no": task.milestone.milestone_no if task.milestone else None,
                    "milestone_name": task.milestone.name if task.milestone else None,
                    "task_id": task.id,
                    "task_title": task.title,
                    "task_status": task.status,
                    "timer_state": timer_state,
                    "current_session_start_time": active_log.start_time if active_log else None,
                    "current_session_seconds": current_session_seconds,
                    "current_session_display": humanize_duration(current_session_seconds),
                    "last_session_end_time": last_completed_log.end_time if last_completed_log else None,
                    "last_session_start_time": last_completed_log.start_time if last_completed_log else None,
                    "last_stop_source": last_completed_log.source if last_completed_log else None,
                    "today_worked_seconds": today_worked_seconds,
                    "today_worked_display": humanize_duration(today_worked_seconds),
                    "total_time_spent_seconds": total_tracked_seconds,
                    "total_time_spent_display": humanize_duration(total_tracked_seconds),
                    "history": history,
                }
            )

        data = {
            "filters": {
                "employee_id": employee_id,
                "project_id": project_id,
                "milestone_id": milestone_id,
                "task_id": task_id,
                "status": status_filter,
                "only_active": only_active,
                "date_from": date_from_raw,
                "date_to": date_to_raw,
            },
            "summary": {
                "records_count": len(records),
                "started_count": len([item for item in records if item["timer_state"] == "STARTED"]),
                "paused_count": len([item for item in records if item["timer_state"] == "PAUSED"]),
                "stopped_count": len([item for item in records if item["timer_state"] == "STOPPED"]),
                "not_started_count": len([item for item in records if item["task_status"] == Task.Status.NOT_STARTED]),
                "delayed_count": len([item for item in records if item["task_status"] == Task.Status.DELAYED]),
                "completed_count": len([item for item in records if item["task_status"] == Task.Status.COMPLETED]),
                "auto_stopped_count": len(
                    [item for item in records if item["timer_state"] == "AUTO_STOPPED"]
                ),
            },
            "recent_activity": self._build_recent_activity(tasks_qs, role, request.user),
        }
        page, paginator = paginate_request(request, records)
        paged_records = page if page is not None else records
        data["work_tracking"] = paged_records
        if paginator is not None:
            return Response(
                {
                    "success": True,
                    "message": "Work tracking fetched.",
                    "code": status.HTTP_200_OK,
                    "data": data,
                    "meta": paginator._meta_payload(),
                }
            )
        return api_response(True, "Work tracking fetched.", status.HTTP_200_OK, data)

    def _build_recent_activity(self, tasks_qs, role, actor):
        task_ids = list(tasks_qs.values_list("id", flat=True))
        window_start = _recent_activity_window_start()

        if not task_ids:
            if role in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}:
                return _build_notification_recent_activity(actor, window_start)[:20]
            return []

        logs_qs = (
            TimeLog.objects.select_related("task", "task__project", "user")
            .filter(task_id__in=task_ids)
            .filter(Q(start_time__gte=window_start) | Q(end_time__gte=window_start))
            .order_by("-start_time")
        )
        if role == UserProfile.Roles.BA:
            logs_qs = logs_qs.filter(Q(task__created_by=actor) | Q(task__supervisor=actor))
        elif role == UserProfile.Roles.EMPLOYEE:
            logs_qs = logs_qs.filter(user=actor)

        events = []
        for log in logs_qs[:80]:
            user_name = log.user.get_full_name().strip() or log.user.email
            events.append(
                {
                    "action": "STARTED",
                    "employee_name": user_name,
                    "task_id": log.task_id,
                    "task_title": log.task.title,
                            "project_name": _task_project_name(log.task),
                    "timestamp": log.start_time,
                }
            )
            if log.end_time:
                if log.source == TimeLog.Source.MANUAL_PAUSE:
                    action = "PAUSED"
                else:
                    action = "STOPPED"
                events.append(
                    {
                        "action": action,
                        "employee_name": user_name,
                        "task_id": log.task_id,
                        "task_title": log.task.title,
                        "project_name": _task_project_name(log.task),
                        "timestamp": log.end_time,
                    }
                )

        completed_qs = Task.objects.select_related("assigned_to", "project").filter(
            id__in=task_ids,
            status=Task.Status.COMPLETED,
            updated_at__gte=window_start,
        )
        if role == UserProfile.Roles.BA:
            completed_qs = completed_qs.filter(Q(created_by=actor) | Q(supervisor=actor))
        elif role == UserProfile.Roles.EMPLOYEE:
            completed_qs = completed_qs.filter(assigned_to=actor)
        for task in completed_qs[:40]:
            employee = task.assigned_to or task.created_by
            employee_name = (
                employee.get_full_name().strip() or employee.email
                if employee
                else "Employee"
            )
            events.append(
                {
                    "action": "COMPLETED",
                    "employee_name": employee_name,
                    "task_id": task.id,
                    "task_title": task.title,
                    "project_name": _task_project_name(task),
                    "timestamp": task.updated_at,
                }
            )

        notification_events = []
        if role in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}:
            notification_events = _build_notification_recent_activity(actor, window_start)

        return _merge_recent_activity_events(events, notification_events, limit=20)


@extend_schema(tags=["Admin APIs"])
class AdminOverviewAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        project_id = request.query_params.get("project_id")
        milestone_id = request.query_params.get("milestone_id")
        task_id = request.query_params.get("task_id")
        data = build_admin_overview_payload(project_id=project_id, milestone_id=milestone_id, task_id=task_id)
        return api_response(True, "Admin overview fetched.", status.HTTP_200_OK, data)


@extend_schema(tags=["Admin APIs"], responses={200: OpenApiTypes.OBJECT})
class AdminAIHealthAPIView(APIView):
    """
    Admin-only: verify connectivity to the configured AI provider (Sarvam or Ollama).
    Configure AI_PROVIDER and SARVAM_* or OLLAMA_* in pms/.env.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        provider = get_ai_provider()
        try:
            health = llm_health()
        except LLMClientError as e:
            cfg = get_sarvam_settings() if provider == "sarvam" else {}
            configured = bool(str(cfg.get("api_key", "")).strip()) if provider == "sarvam" else True
            return api_response(
                True if configured else False,
                str(e) or "AI provider is not reachable.",
                status.HTTP_200_OK if configured else status.HTTP_502_BAD_GATEWAY,
                {
                    "provider": provider,
                    "reachable": False,
                    "configured": configured,
                    "base_url": cfg.get("base_url") if provider == "sarvam" else None,
                    "configured_model": cfg.get("model") if provider == "sarvam" else None,
                    "model_available": False,
                    "network_error": True,
                },
            )
        if not health.get("configured", True):
            return api_response(
                False,
                health.get("message") or "AI provider is not configured.",
                status.HTTP_502_BAD_GATEWAY,
                health,
            )
        if not health.get("model_available"):
            return api_response(
                False,
                f"Model {health.get('configured_model')!r} is not available.",
                status.HTTP_502_BAD_GATEWAY,
                health,
            )
        label = "Sarvam" if provider == "sarvam" else "Ollama"
        return api_response(
            True,
            f"{label} is connected.",
            status.HTTP_200_OK,
            health,
        )


@extend_schema(
    tags=["Admin APIs"],
    request=AdminAIAskSerializer,
    responses={200: OpenApiTypes.OBJECT},
    examples=[
        OpenApiExample(
            "Question only (default)",
            value={"question": "How many active projects are there?"},
            request_only=True,
        ),
    ],
)
class AdminAIAskAPIView(APIView):
    """
    Admin-only: read-only DB snapshot (ORM) is sent to Sarvam/Ollama; the model answers in plain English.
    No create/update/delete — answers only. Configure AI_PROVIDER and SARVAM_* in pms/.env.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request):
        serializer = AdminAIAskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data["question"]
        project_id = serializer.validated_data.get("project_id")
        milestone_id = serializer.validated_data.get("milestone_id")
        task_id = serializer.validated_data.get("task_id")
        if project_id is not None and not Project.objects.filter(id=project_id).exists():
            return api_response(False, "Project not found.", status.HTTP_400_BAD_REQUEST, None)
        if milestone_id is not None and not Milestone.objects.filter(id=milestone_id).exists():
            return api_response(False, "Milestone not found.", status.HTTP_400_BAD_REQUEST, None)
        if task_id is not None and not Task.objects.filter(id=task_id).exists():
            return api_response(False, "Task not found.", status.HTTP_400_BAD_REQUEST, None)

        if is_write_intent(question):
            return api_response(
                True,
                "Read-only assistant.",
                status.HTTP_200_OK,
                {
                    "answer": READ_ONLY_REFUSAL,
                    "model": "read-only-guard",
                    "provider": get_ai_provider(),
                },
            )

        context_payload = build_readonly_context_payload(
            project_id=project_id,
            milestone_id=milestone_id,
            task_id=task_id,
            question=question,
            asking_user=request.user,
        )

        greeting_reply = try_greeting_reply(question, context_payload)
        if greeting_reply:
            return api_response(
                True,
                "Greeting.",
                status.HTTP_200_OK,
                {
                    "answer": greeting_reply,
                    "model": "greeting",
                    "provider": get_ai_provider(),
                },
            )

        self_reply = try_self_identity_reply(question, context_payload)
        if self_reply:
            return api_response(
                True,
                "Self identity answer.",
                status.HTTP_200_OK,
                {
                    "answer": self_reply,
                    "model": "self-identity",
                    "provider": get_ai_provider(),
                },
            )

        role_reply = try_role_count_reply(question, context_payload)
        if role_reply:
            return api_response(
                True,
                "Role count answer.",
                status.HTTP_200_OK,
                {
                    "answer": role_reply,
                    "model": "role-count",
                    "provider": get_ai_provider(),
                },
            )

        yesterday_reply = try_yesterday_attendance_reply(question, context_payload)
        if yesterday_reply:
            return api_response(
                True,
                "Yesterday attendance answer.",
                status.HTTP_200_OK,
                {
                    "answer": yesterday_reply,
                    "model": "attendance-yesterday",
                    "provider": get_ai_provider(),
                },
            )

        task_count_reply = try_employee_task_count_reply(question, context_payload)
        if task_count_reply:
            return api_response(
                True,
                "Employee task count answer.",
                status.HTTP_200_OK,
                {
                    "answer": task_count_reply,
                    "model": "employee-tasks",
                    "provider": get_ai_provider(),
                },
            )

        performance_reply = try_employee_performance_reply(question, context_payload)
        if performance_reply:
            return api_response(
                True,
                "Employee performance answer.",
                status.HTTP_200_OK,
                {
                    "answer": performance_reply,
                    "model": "employee-performance",
                    "provider": get_ai_provider(),
                },
            )

        period_report_reply = try_employee_period_report_reply(question, context_payload)
        if period_report_reply:
            return api_response(
                True,
                "Employee period report.",
                status.HTTP_200_OK,
                {
                    "answer": period_report_reply,
                    "model": "employee-period-report",
                    "provider": get_ai_provider(),
                },
            )

        context_text = json.dumps(context_payload, default=str, ensure_ascii=False)
        attendance_available = bool(context_payload.get("attendance_data_available"))
        system = build_system_prompt(attendance_available=attendance_available)
        user_msg = build_user_message(
            context_text=context_text,
            question=question,
            attendance_focus=is_attendance_question(question),
        )
        try:
            answer, model, provider = llm_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
            )
        except LLMClientError as e:
            logger.exception("AI request failed (%s): %s", get_ai_provider(), e)
            return api_response(
                False,
                str(e) or "AI request failed.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
                {
                    "provider": get_ai_provider(),
                    "hint": (
                        "Sarvam cloud needs internet/DNS access to api.sarvam.ai. "
                        "Set AI_FALLBACK_TO_OLLAMA=True and ensure Ollama is running, "
                        "or fix network/DNS on this server."
                    ),
                },
            )
        if not answer:
            return api_response(
                False,
                "AI returned an empty response.",
                status.HTTP_502_BAD_GATEWAY,
                None,
            )
        return api_response(
            True,
            "AI answer generated.",
            status.HTTP_200_OK,
            {
                "answer": answer,
                "model": model,
                "provider": provider,
            },
        )


@extend_schema(tags=["Common APIs"])
class FirstLoginRequestOTPAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=FirstLoginRequestOTPSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = FirstLoginRequestOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower().strip()
        target_user = User.objects.filter(
            email__iexact=email,
            profile__status=UserProfile.Status.ACTIVE,
        ).first()
        if not target_user:
            return api_response(False, "User with this email was not found.", status.HTTP_404_NOT_FOUND)

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        if profile.password_set:
            return api_response(
                False,
                "Password is already set. Please use regular login.",
                status.HTTP_400_BAD_REQUEST,
                {"password_set": True},
            )

        # Keep legacy OTP fields clear; first-login now uses invite-link token flow.
        profile.first_login_otp = ""
        profile.first_login_otp_expires_at = None
        profile.save(update_fields=["first_login_otp", "first_login_otp_expires_at"])
        send_user_first_login_email(target_user)

        return api_response(
            True,
            "First-login link sent successfully. Mail sent successfully.",
            status.HTTP_200_OK,
            {"email": email, "mail_triggered": True, "expires_in_hours": 24},
        )


@extend_schema(tags=["Common APIs"])
class FirstLoginVerifyOTPAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=FirstLoginTokenVerifySerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = FirstLoginTokenVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["token"]
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        target_user = User.objects.filter(
            profile__first_login_token_hash=token_hash,
            profile__status=UserProfile.Status.ACTIVE,
        ).first()
        if not target_user:
            return api_response(False, "Invalid first-login token.", status.HTTP_400_BAD_REQUEST)

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        if profile.password_set:
            return api_response(
                False,
                "Password is already set. Please use regular login.",
                status.HTTP_400_BAD_REQUEST,
            )
        if not profile.first_login_token_hash or token_hash != profile.first_login_token_hash:
            return api_response(False, "Invalid first-login token.", status.HTTP_400_BAD_REQUEST)
        if not profile.first_login_token_expires_at or timezone.now() > profile.first_login_token_expires_at:
            return api_response(
                False,
                "First-login token expired. Please request a new link.",
                status.HTTP_400_BAD_REQUEST,
                {"token_expired": True, "can_resend": True},
            )

        return api_response(
            True,
            "Token verified successfully. Please set your password.",
            status.HTTP_200_OK,
            {"token_verified": True},
        )


@extend_schema(tags=["Common APIs"])
class FirstLoginSetPasswordAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=FirstLoginSetPasswordSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = FirstLoginSetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["token"]
        new_password = serializer.validated_data["new_password"]
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        target_user = User.objects.filter(
            profile__first_login_token_hash=token_hash,
            profile__status=UserProfile.Status.ACTIVE,
        ).first()
        if not target_user:
            return api_response(False, "Invalid first-login token.", status.HTTP_400_BAD_REQUEST)

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        if profile.password_set:
            return api_response(False, "Password is already set. Please use regular login.", status.HTTP_400_BAD_REQUEST)

        if not profile.first_login_token_hash or token_hash != profile.first_login_token_hash:
            return api_response(False, "Invalid first-login token.", status.HTTP_400_BAD_REQUEST)
        if not profile.first_login_token_expires_at or timezone.now() > profile.first_login_token_expires_at:
            return api_response(
                False,
                "First-login token expired. Please request a new link.",
                status.HTTP_400_BAD_REQUEST,
                {"token_expired": True, "can_resend": True},
            )

        target_user.set_password(new_password)
        target_user.save(update_fields=["password"])
        profile.password_set = True
        profile.first_login_otp = ""
        profile.first_login_otp_expires_at = None
        profile.first_login_token_hash = ""
        profile.first_login_token_expires_at = None
        profile.save(
            update_fields=[
                "password_set",
                "first_login_otp",
                "first_login_otp_expires_at",
                "first_login_token_hash",
                "first_login_token_expires_at",
            ]
        )

        return api_response(
            True,
            "Password set successfully. Redirect to sign-in page.",
            status.HTTP_200_OK,
            {"email": target_user.email, "redirect_url": getattr(settings, "FRONTEND_LOGIN_URL", "")},
        )


@extend_schema(tags=["Common APIs"])
class FirstLoginResendLinkAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=FirstLoginResendLinkSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = FirstLoginResendLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_hash = hashlib.sha256(serializer.validated_data["token"].encode()).hexdigest()
        target_user = User.objects.filter(
            profile__first_login_token_hash=token_hash,
            profile__status=UserProfile.Status.ACTIVE,
        ).first()
        if not target_user:
            return api_response(False, "Invalid first-login token.", status.HTTP_400_BAD_REQUEST)

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        if profile.password_set:
            return api_response(
                False,
                "Password is already set. Please use regular login.",
                status.HTTP_400_BAD_REQUEST,
                {"password_set": True},
            )

        send_user_first_login_email(target_user)
        return api_response(
            True,
            "A new first-login link has been sent successfully.",
            status.HTTP_200_OK,
            {"mail_triggered": True, "expires_in_hours": 24},
        )


@extend_schema(tags=["Admin APIs"], request=AdminPasswordResetSerializer)
class AdminPasswordResetAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    @extend_schema(request=AdminPasswordResetSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = AdminPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        new_password = serializer.validated_data["new_password"]

        target_user = User.objects.filter(email__iexact=email).first()
        if not target_user:
            return api_response(False, "User with this email was not found.", status.HTTP_404_NOT_FOUND)

        target_user.set_password(new_password)
        target_user.save(update_fields=["password"])
        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        profile.password_set = True
        profile.password_reset_otp = ""
        profile.password_reset_otp_expires_at = None
        profile.save(update_fields=["password_set", "password_reset_otp", "password_reset_otp_expires_at"])
        return api_response(True, "Password updated successfully.", status.HTTP_200_OK, {"email": target_user.email})


@extend_schema(tags=["Common APIs"])
class AdminForgotPasswordRequestOTPAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=AdminForgotPasswordRequestSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = AdminForgotPasswordRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower().strip()
        target_user = User.objects.filter(
            email__iexact=email,
            profile__status=UserProfile.Status.ACTIVE,
        ).first()
        if not target_user:
            return api_response(False, "User with this email was not found.", status.HTTP_404_NOT_FOUND)

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        if not profile.password_set:
            return api_response(
                False,
                "First-time login users must complete OTP first-login setup.",
                status.HTTP_400_BAD_REQUEST,
                {"first_login_required": True, "email": email},
            )

        otp = f"{secrets.randbelow(10**6):06d}"
        profile.password_reset_otp = otp
        profile.password_reset_otp_expires_at = timezone.now() + timedelta(seconds=ADMIN_RESET_OTP_TTL_SECONDS)
        profile.save(update_fields=["password_reset_otp", "password_reset_otp_expires_at"])
        send_admin_reset_otp_email(target_user, otp)

        return api_response(
            True,
            "OTP sent successfully. Mail sent successfully.",
            status.HTTP_200_OK,
            {"email": email, "mail_triggered": True},
        )


@extend_schema(tags=["Common APIs"])
class AdminForgotPasswordVerifyOTPAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=AdminForgotPasswordVerifySerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = AdminForgotPasswordVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower().strip()
        otp = serializer.validated_data["otp"]
        new_password = serializer.validated_data["new_password"]

        target_user = User.objects.filter(
            email__iexact=email,
            profile__status=UserProfile.Status.ACTIVE,
        ).first()
        if not target_user:
            return api_response(False, "Invalid email or OTP.", status.HTTP_400_BAD_REQUEST)

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        if not profile.password_set:
            return api_response(
                False,
                "First-time login users must complete OTP first-login setup.",
                status.HTTP_400_BAD_REQUEST,
                {"first_login_required": True, "email": email},
            )

        if (
            not profile.password_reset_otp
            or profile.password_reset_otp != otp
            or not profile.password_reset_otp_expires_at
            or timezone.now() > profile.password_reset_otp_expires_at
        ):
            return api_response(False, "Invalid or expired OTP.", status.HTTP_400_BAD_REQUEST)

        target_user.set_password(new_password)
        target_user.save(update_fields=["password"])
        profile.password_reset_otp = ""
        profile.password_reset_otp_expires_at = None
        profile.save(update_fields=["password_reset_otp", "password_reset_otp_expires_at"])
        return api_response(True, "Password reset successful.", status.HTTP_200_OK, {"email": target_user.email})


#my tasks api view
@extend_schema(tags=["Employee APIs"])
class MyTasksAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        tasks_qs = apply_work_history_retention(
            Task.objects.select_related(
                "project",
                "milestone",
                "milestone__project",
                "created_by",
                "assigned_to",
            )
            .prefetch_related("project__files")
            .filter(assigned_to=request.user)
        ).order_by("-created_at")
        page, paginator = paginate_request(request, tasks_qs)
        targets = page if page is not None else tasks_qs
        serializer = TaskSerializer(targets, many=True, context={"request": request})
        if paginator is not None:
            return paginator.get_paginated_response(
                serializer.data,
                message="My tasks fetched.",
            )
        return unpaginated_list_response(serializer.data, message="My tasks fetched.")
