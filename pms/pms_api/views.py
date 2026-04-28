from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from html import escape
from datetime import datetime, time
import logging
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


from .ai_readonly_context import build_readonly_context_text
from .models import FileAttachment, Milestone, Notification, Project, Task, TimeLog, UserProfile, humanize_duration
from .ollama_client import OllamaClientError, ollama_chat
from .pagination import StandardResultsSetPagination
from .permissions import IsAdmin, IsAdminOrBA, user_role
from .serializers import (
    AdminAIAskSerializer,
    AuthLoginSerializer,
    AuthResponseSerializer,
    AdminForgotPasswordRequestSerializer,
    AdminForgotPasswordVerifySerializer,
    AdminPasswordResetSerializer,
    DeadlineChangeRequestSerializer,
    DeleteRequestSerializer,
    FileAttachmentSerializer,
    FileUploadRequestSerializer,
    MilestoneSerializer,
    MeUpdateSerializer,
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


def _render_email_html(subject, greeting, intro_text, detail_rows, footer_note="Regards,<br>PMS Team"):
    detail_items = "".join(
        f"""
        <tr>
          <td style="padding:8px 0;color:#6b7280;font-size:14px;font-weight:600;vertical-align:top;width:180px;">{escape(str(label))}</td>
          <td style="padding:8px 0;color:#111827;font-size:14px;">{escape(str(value))}</td>
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


def _sync_parent_statuses_for_task(task: Task) -> None:
    """Keep milestone/project statuses aligned with underlying task statuses."""
    today = timezone.localdate()

    def _resolve_status(tasks_qs):
        total = tasks_qs.count()
        if total == 0:
            return "NOT_STARTED"
        completed = tasks_qs.filter(status=Task.Status.COMPLETED).count()
        if completed == total:
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
        return "NOT_STARTED"

    if task.milestone_id:
        ms_tasks = Task.objects.filter(milestone_id=task.milestone_id)
        ms_state = _resolve_status(ms_tasks)
        milestone = task.milestone
        if milestone:
            mapped_ms_status = (
                Milestone.Status.COMPLETED
                if ms_state == "COMPLETED"
                else Milestone.Status.DELAYED
                if ms_state == "DELAYED"
                else Milestone.Status.IN_PROGRESS
                if ms_state == "IN_PROGRESS"
                else Milestone.Status.NOT_STARTED
            )
            if milestone.status != mapped_ms_status:
                milestone.status = mapped_ms_status
                milestone.save(update_fields=["status"])

    prj_tasks = Task.objects.filter(project_id=task.project_id)
    prj_state = _resolve_status(prj_tasks)
    project = task.project
    if project:
        mapped_project_status = (
            Project.Status.COMPLETED
            if prj_state == "COMPLETED"
            else Project.Status.DELAYED
            if prj_state == "DELAYED"
            else Project.Status.ACTIVE
            if prj_state == "IN_PROGRESS"
            else Project.Status.PLANNED
        )
        if project.status != mapped_project_status:
            project.status = mapped_project_status
            project.save(update_fields=["status"])


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
                "project_name": task.project.name,
                "milestone_id": task.milestone_id,
                "milestone_no": task.milestone.milestone_no if task.milestone else None,
                "milestone_name": task.milestone.name if task.milestone else None,
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

def admin_otp_cache_key(email):
    return f"admin_reset_otp:{email.lower().strip()}"


def send_admin_reset_otp_email(user, otp):
    if not user.email:
        return
    subject = "PMS Admin Password Reset OTP"
    greeting = f"Hi {user.first_name or user.username},"
    intro_text = "Use the OTP below to reset your admin account password."
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


def send_task_completed_email(task, completed_by):
    owner = task.created_by
    if not owner or not owner.email:
        return
    completed_by_name = completed_by.get_full_name().strip() or completed_by.email
    subject = f"Task Completed: {task.title}"
    greeting = f"Hi {owner.first_name or owner.username},"
    intro_text = "A task created by you is now marked as completed."
    detail_rows = [
        ("Task", task.title),
        ("Completed By", completed_by_name),
        ("Current Status", task.status),
    ]
    try:
        _send_styled_email(subject, [owner.email], greeting, intro_text, detail_rows)
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


def send_project_change_request_email(project, requester, admin_emails, request_type, new_deadline=None, reason=""):
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
        return [IsAuthenticated(), IsAdmin()]

    def create(self, request, *args, **kwargs):
        raw_password = request.data.get("password")
        serializer = UserCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        payload = UserSerializer(user).data
        message = "User created successfully."
        if raw_password:
            send_user_welcome_email(user, raw_password)
            message = "User created successfully. Mail sent successfully."
            payload["mail_triggered"] = True
        else:
            payload["mail_triggered"] = False
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




#project view set pagination
@extend_schema(tags=["BA/Admin APIs"])
class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all().order_by("-created_at")
    serializer_class = ProjectSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        if user_role(self.request.user) == UserProfile.Roles.EMPLOYEE:
            return queryset.filter(tasks__assigned_to=self.request.user).distinct()
        return queryset

    def get_permissions(self):
        if self.action == "destroy":
            return [IsAuthenticated(), IsAdmin()]
        if self.action in {"create", "update", "partial_update", "request_deadline_change"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        if self.action == "request_delete":
            return [IsAuthenticated(), IsAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

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

        admin_users = User.objects.filter(
            profile__role=UserProfile.Roles.ADMIN,
            profile__status=UserProfile.Status.ACTIVE,
        )
        admin_emails = [user.email for user in admin_users if user.email]
        for admin_user in admin_users:
            Notification.objects.create(
                user=admin_user,
                type="PROJECT_DEADLINE_CHANGE_REQUEST",
                title="Project deadline change requested",
                message=f"BA requested deadline change for project '{project.name}'.",
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
                "new_deadline": new_deadline,
                "reason": reason,
                "mail_triggered": mail_triggered,
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
        admin_emails = [user.email for user in admin_users if user.email]
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

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)





#task view set pagination
@extend_schema(tags=["BA/Employee APIs"])
class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.select_related("project", "milestone", "assigned_to").all().order_by("-created_at")
    serializer_class = TaskSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

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
        if self.action == "request_deadline_change":
            return [IsAuthenticated()]
        if self.action in {"create", "update", "partial_update", "destroy", "assign", "request_deadline_change"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        return [IsAuthenticated()]

    def _allowed_assignee_roles(self):
        actor_role = user_role(self.request.user)
        if actor_role == UserProfile.Roles.ADMIN:
            return {UserProfile.Roles.BA, UserProfile.Roles.EMPLOYEE}
        if actor_role == UserProfile.Roles.BA:
            return {UserProfile.Roles.EMPLOYEE}
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

    def _validate_project_milestone_scope(self, project, milestone):
        actor = self.request.user
        actor_role = user_role(actor)

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

    def perform_create(self, serializer):
        self._reset_mail_state()
        project = serializer.validated_data.get("project")
        milestone = serializer.validated_data.get("milestone")
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

    def perform_update(self, serializer):
        self._reset_mail_state()
        previous_assignee_id = getattr(serializer.instance, "assigned_to_id", None)
        project = serializer.validated_data.get("project", serializer.instance.project)
        milestone = serializer.validated_data.get("milestone", serializer.instance.milestone)
        self._validate_project_milestone_scope(project, milestone)
        assignee = serializer.validated_data.get("assigned_to", serializer.instance.assigned_to)
        self._validate_assignee(assignee)
        requested_status = serializer.validated_data.get("status", serializer.instance.status)
        if (
            requested_status == Task.Status.COMPLETED
            and TimeLog.objects.filter(task=serializer.instance, end_time__isnull=True).exists()
        ):
            raise ValidationError({"status": "Stop the active timer first, then mark task as completed."})
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
        if user_role(request.user) != UserProfile.Roles.EMPLOYEE or task.assigned_to_id != request.user.id:
            return api_response(False, "Only assigned employee can start this task.", status.HTTP_403_FORBIDDEN)
        if TimeLog.objects.filter(user=request.user, end_time__isnull=True).exists():
            return api_response(False, "You already have an active timer.", status.HTTP_400_BAD_REQUEST)
        TimeLog.objects.create(task=task, user=request.user, start_time=timezone.now())
        task.status = Task.Status.IN_PROGRESS
        task.save(update_fields=["status"])
        _sync_parent_statuses_for_task(task)
        return api_response(True, "Task started.", status.HTTP_200_OK, {"task_id": task.id, "status": task.status})

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        task = self.get_object()
        log = TimeLog.objects.filter(task=task, user=request.user, end_time__isnull=True).last()
        if not log:
            return api_response(False, "No active timer found for this task.", status.HTTP_400_BAD_REQUEST)
        log.stop(source=TimeLog.Source.MANUAL_PAUSE)
        task.status = Task.Status.PAUSED
        task.save(update_fields=["status", "total_time_spent_seconds"])
        _sync_parent_statuses_for_task(task)
        return api_response(True, "Task paused.", status.HTTP_200_OK, {"task_id": task.id, "status": task.status})

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        task = self.get_object()
        log = TimeLog.objects.filter(task=task, user=request.user, end_time__isnull=True).last()
        if not log:
            return api_response(False, "No active timer found for this task.", status.HTTP_400_BAD_REQUEST)
        log.stop(source=TimeLog.Source.MANUAL_STOP)
        if task.status != Task.Status.COMPLETED:
            task.status = Task.Status.PAUSED
            task.save(update_fields=["status", "total_time_spent_seconds"])
        _sync_parent_statuses_for_task(task)
        if user_role(request.user) == UserProfile.Roles.EMPLOYEE:
            return api_response(
                True,
                "Task stopped.",
                status.HTTP_200_OK,
                {"task_id": task.id, "status": task.status},
            )
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
        logs = task.time_logs.all().order_by("-created_at")
        history = logs.aggregate(
            start_count=Count("id"),
            pause_count=Count("id", filter=Q(source=TimeLog.Source.MANUAL_PAUSE)),
            stop_count=Count("id", filter=Q(source=TimeLog.Source.MANUAL_STOP)),
        )
        return api_response(
            True,
            "Time logs fetched.",
            status.HTTP_200_OK,
            {
                "history": history,
                "time_logs": TimeLogSerializer(logs, many=True).data,
            },
        )

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        task = self.get_object()
        status_value = request.data.get("status")
        if status_value not in Task.Status.values:
            return api_response(False, "Invalid status.", status.HTTP_400_BAD_REQUEST)
        if (
            status_value == Task.Status.COMPLETED
            and TimeLog.objects.filter(task=task, end_time__isnull=True).exists()
        ):
            return api_response(
                False,
                "Stop the active timer first, then mark task as completed.",
                status.HTTP_400_BAD_REQUEST,
            )
        task.status = status_value
        task.save(update_fields=["status"])
        _sync_parent_statuses_for_task(task)
        mail_triggered = False
        if status_value == Task.Status.COMPLETED and task.created_by:
            Notification.objects.create(
                user=task.created_by,
                type="TASK_COMPLETED",
                title="Task Completed",
                message=f"Task '{task.title}' has been completed.",
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
        owner = task.created_by
        mail_triggered = False

        if owner:
            Notification.objects.create(
                user=owner,
                type="TASK_DEADLINE_CHANGE_REQUEST",
                title="Task deadline change requested",
                message=f"Employee requested deadline change for task '{task.title}'.",
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
            return base_qs.filter(
                Q(task__assigned_to=self.request.user)
                | Q(milestone__tasks__assigned_to=self.request.user)
                | Q(project__tasks__assigned_to=self.request.user)
            ).distinct()
        return base_qs

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

            # BA dashboard should include BA-created + Admin-created scope.
            ba_tasks_qs = Task.objects.filter(created_by_id__in=allowed_creator_ids).select_related(
                "project", "milestone", "assigned_to"
            )
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
                                "project_name": task.project.name,
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
                        "project_name": log.task.project.name,
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
                            "project_name": log.task.project.name,
                            "timestamp": log.end_time,
                        }
                    )
            recent_activity.sort(key=lambda item: item["timestamp"], reverse=True)
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
                        "project_name": task.project.name,
                        "milestone_name": task.milestone.name if task.milestone else None,
                        "status": task.status,
                    }
                    for task in ba_tasks_qs.order_by("-updated_at")
                ],
                "recent_activity": recent_activity[:20],
                "employee_summary": employee_summary,
            }
            return api_response(True, "BA dashboard fetched.", status.HTTP_200_OK, data)
        data = {
            "active_task": Task.objects.filter(assigned_to=user, status=Task.Status.IN_PROGRESS).values("id", "title").first(),
            "completed_tasks": Task.objects.filter(assigned_to=user, status=Task.Status.COMPLETED).count(),
        }
        return api_response(True, "Employee dashboard fetched.", status.HTTP_200_OK, data)





@extend_schema(tags=["BA/Admin APIs"])
class WorkTrackingAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrBA]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        apply_automatic_task_status_rules()
        role = user_role(request.user)
        if role not in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}:
            return api_response(False, "Only Admin or BA can access work tracking.", status.HTTP_403_FORBIDDEN)

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
            tasks_qs = tasks_qs.filter(created_by_id__in=allowed_creator_ids)

        employee_id = request.query_params.get("employee_id")
        project_id = request.query_params.get("project_id")
        milestone_id = request.query_params.get("milestone_id")
        task_id = request.query_params.get("task_id")
        status_filter = request.query_params.get("status")
        only_active = request.query_params.get("only_active")

        if employee_id:
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
            if active_log:
                timer_state = "STARTED"
            elif task.status == Task.Status.PAUSED:
                timer_state = "PAUSED"
            else:
                timer_state = "STOPPED"

            if only_active and only_active.lower() == "true" and timer_state != "STARTED":
                continue

            todays_logs = TimeLog.objects.filter(task=task, user=task.assigned_to, start_time__date=today)
            today_worked_seconds = sum(todays_logs.values_list("duration_seconds", flat=True))
            current_session_seconds = 0
            if active_log:
                current_session_seconds = int((now - active_log.start_time).total_seconds())
                if active_log.start_time.date() == today:
                    today_worked_seconds += current_session_seconds

            last_completed_log = (
                TimeLog.objects.filter(task=task, user=task.assigned_to, end_time__isnull=False).order_by("-end_time").first()
            )
            history = TimeLog.objects.filter(task=task, user=task.assigned_to).aggregate(
                start_count=Count("id"),
                pause_count=Count("id", filter=Q(source=TimeLog.Source.MANUAL_PAUSE)),
                stop_count=Count("id", filter=Q(source=TimeLog.Source.MANUAL_STOP)),
            )

            records.append(
                {
                    "employee_id": task.assigned_to_id,
                    "employee_name": task.assigned_to.get_full_name().strip() or task.assigned_to.email,
                    "employee_email": task.assigned_to.email,
                    "project_id": task.project_id,
                    "project_name": task.project.name,
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
                    "today_worked_seconds": today_worked_seconds,
                    "today_worked_display": humanize_duration(today_worked_seconds),
                    "total_time_spent_seconds": task.total_time_spent_seconds,
                    "total_time_spent_display": humanize_duration(task.total_time_spent_seconds),
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
            },
            "summary": {
                "records_count": len(records),
                "started_count": len([item for item in records if item["timer_state"] == "STARTED"]),
                "paused_count": len([item for item in records if item["timer_state"] == "PAUSED"]),
                "stopped_count": len([item for item in records if item["timer_state"] == "STOPPED"]),
            },
            "work_tracking": records,
            "recent_activity": self._build_recent_activity(tasks_qs, role, request.user),
        }
        return api_response(True, "Work tracking fetched.", status.HTTP_200_OK, data)

    def _build_recent_activity(self, tasks_qs, role, actor):
        task_ids = list(tasks_qs.values_list("id", flat=True))
        if not task_ids:
            return []
        now_local = timezone.localtime()
        day_start = datetime.combine(now_local.date(), time.min, tzinfo=now_local.tzinfo)
        reset_time = datetime.combine(now_local.date(), time(hour=22), tzinfo=now_local.tzinfo)
        window_start = reset_time if now_local >= reset_time else day_start

        logs_qs = (
            TimeLog.objects.select_related("task", "task__project", "user")
            .filter(task_id__in=task_ids)
            .filter(Q(start_time__gte=window_start) | Q(end_time__gte=window_start))
            .order_by("-start_time")
        )
        if role == UserProfile.Roles.BA:
            logs_qs = logs_qs.filter(task__created_by=actor)

        events = []
        for log in logs_qs[:80]:
            user_name = log.user.get_full_name().strip() or log.user.email
            events.append(
                {
                    "action": "STARTED",
                    "employee_name": user_name,
                    "task_id": log.task_id,
                    "task_title": log.task.title,
                    "project_name": log.task.project.name,
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
                        "project_name": log.task.project.name,
                        "timestamp": log.end_time,
                    }
                )

        completed_qs = Task.objects.select_related("assigned_to", "project").filter(
            id__in=task_ids,
            status=Task.Status.COMPLETED,
            updated_at__gte=window_start,
        )
        if role == UserProfile.Roles.BA:
            completed_qs = completed_qs.filter(created_by=actor)
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
                    "project_name": task.project.name,
                    "timestamp": task.updated_at,
                }
            )

        events.sort(key=lambda item: item["timestamp"], reverse=True)
        return events[:20]


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
    Admin-only: read-only DB snapshot (ORM) is sent to the local Ollama server; the model answers in plain English.
    No create/update/delete. Configure OLLAMA_BASE_URL, OLLAMA_MODEL (default: gemma4:e2b) in the environment.
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

        context_text = build_readonly_context_text(
            project_id=project_id,
            milestone_id=milestone_id,
            task_id=task_id,
        )
        system = (
            "You are an admin's project assistant. The JSON includes an object ai_briefing with per_project_briefing: "
            "use it for a concise status report in plain English. Prefer naming people from people_assigned_to_tasks, "
            "milestones in progress (milestones_in_progress_by_name), task counts (completed vs remaining_not_done), "
            "and project deadline with days_until_project_deadline. "
            "The full task and milestone lists add detail when needed. You do not have file/website content—only this data. "
            "If something is not in the JSON, say so. Do not invent names, numbers, or dates. "
            "If the question could refer to more than one project in the data, list the short options and ask which one they mean."
        )
        user_msg = f"Data snapshot (JSON, read-only):\n{context_text}\n\nAdmin question: {question}"
        try:
            answer = ollama_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                base_url=getattr(settings, "OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                model=getattr(settings, "OLLAMA_MODEL", "gemma4:e2b"),
                timeout=int(getattr(settings, "OLLAMA_TIMEOUT", 120)),
            )
        except OllamaClientError as e:
            logger.exception("Ollama request failed: %s", e)
            return api_response(
                False,
                str(e) or "Ollama request failed.",
                status.HTTP_502_BAD_GATEWAY,
                None,
            )
        if not answer:
            return api_response(
                False,
                "Ollama returned an empty response.",
                status.HTTP_502_BAD_GATEWAY,
                None,
            )
        return api_response(
            True,
            "AI answer generated.",
            status.HTTP_200_OK,
            {
                "answer": answer,
                "model": getattr(settings, "OLLAMA_MODEL", "gemma4:e2b"),
            },
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
        return api_response(True, "Password updated successfully.", status.HTTP_200_OK, {"email": target_user.email})


@extend_schema(tags=["Common APIs"])
class AdminForgotPasswordRequestOTPAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=AdminForgotPasswordRequestSerializer, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        serializer = AdminForgotPasswordRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower().strip()
        matched_user = User.objects.filter(email__iexact=email).first()
        if not matched_user:
            return api_response(False, "User with this email was not found.", status.HTTP_404_NOT_FOUND)

        role = getattr(getattr(matched_user, "profile", None), "role", None)
        if role != UserProfile.Roles.ADMIN:
            return api_response(False, "Forgot password is allowed only for admin users.", status.HTTP_403_FORBIDDEN)

        admin_user = User.objects.filter(
            id=matched_user.id,
            profile__status=UserProfile.Status.ACTIVE,
        ).first()
        if not admin_user:
            return api_response(False, "Admin account is inactive.", status.HTTP_403_FORBIDDEN)

        otp = f"{secrets.randbelow(10**6):06d}"
        cache.set(admin_otp_cache_key(email), otp, timeout=ADMIN_RESET_OTP_TTL_SECONDS)
        send_admin_reset_otp_email(admin_user, otp)

        return api_response(
            True,
            "Admin OTP sent successfully. Mail sent successfully.",
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

        admin_user = User.objects.filter(
            email__iexact=email,
            profile__role=UserProfile.Roles.ADMIN,
            profile__status=UserProfile.Status.ACTIVE,
        ).first()
        if not admin_user:
            return api_response(False, "Invalid email or OTP.", status.HTTP_400_BAD_REQUEST)

        cached_otp = cache.get(admin_otp_cache_key(email))
        if not cached_otp or cached_otp != otp:
            return api_response(False, "Invalid or expired OTP.", status.HTTP_400_BAD_REQUEST)

        admin_user.set_password(new_password)
        admin_user.save(update_fields=["password"])
        cache.delete(admin_otp_cache_key(email))
        return api_response(True, "Admin password reset successful.", status.HTTP_200_OK, {"email": admin_user.email})


#my tasks api view
@extend_schema(tags=["Employee APIs"])
class MyTasksAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        tasks = Task.objects.filter(assigned_to=request.user).order_by("-created_at")
        serializer = TaskSerializer(tasks, many=True, context={"request": request})
        return api_response(True, "My tasks fetched.", status.HTTP_200_OK, serializer.data)
