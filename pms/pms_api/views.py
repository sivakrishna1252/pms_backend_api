from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
import logging
import secrets
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenRefreshSerializer


from .models import FileAttachment, Milestone, Notification, Project, Task, TimeLog, UserProfile
from .pagination import StandardResultsSetPagination
from .permissions import IsAdmin, IsAdminOrBA, user_role
from .serializers import (
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

def api_response(success, message, code, data=None):
    return Response(
        {"success": success, "message": message, "code": code, "data": data},
        status=code,
    )


def build_admin_overview_payload(project_id=None, milestone_id=None, task_id=None):
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
                "created_by_id": task.created_by_id,
                "created_by_name": task.created_by.get_full_name().strip() or task.created_by.email,
                "assigned_to_id": task.assigned_to_id,
                "assigned_to_name": (
                    (task.assigned_to.get_full_name().strip() or task.assigned_to.email) if task.assigned_to else None
                ),
                "total_time_spent_seconds": task.total_time_spent_seconds,
            }
            for task in tasks_qs.order_by("-updated_at")
        ],
    }

def send_task_assignment_email(employee, task):
    subject = f"Task Assigned: {task.title}"
    message = (
        f"Hi {employee.first_name or employee.username},\n\n"
        f"A task has been assigned to you.\n\n"
        f"Task: {task.title}\n"
        f"Description: {task.description or 'N/A'}\n"
        f"Status: {task.status}\n\n"
        "Please check your dashboard and start work as scheduled.\n\n"
        "Regards,\nPMS Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[employee.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send task assignment email to %s for task %s", employee.email, task.id)

def admin_otp_cache_key(email):
    return f"admin_reset_otp:{email.lower().strip()}"


def send_admin_reset_otp_email(user, otp):
    if not user.email:
        return
    subject = "PMS Admin Password Reset OTP"
    message = (
        f"Hi {user.first_name or user.username},\n\n"
        "Use the OTP below to reset your admin account password.\n\n"
        f"OTP: {otp}\n"
        f"This OTP is valid for {ADMIN_RESET_OTP_TTL_SECONDS // 60} minutes.\n\n"
        "If you did not request this, please ignore this email.\n\n"
        "Regards,\nPMS Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send admin reset OTP email to %s", user.email)


def send_user_welcome_email(user, raw_password):
    if not user.email:
        return
    subject = "Your PMS account is created"
    message = (
        f"Hi {user.first_name or user.username},\n\n"
        "Your Project Management System account has been created.\n\n"
        f"Login Email: {user.email}\n"
        f"Temporary Password: {raw_password}\n\n"
        "Please login to PMS dashboard \n\n"
        "Regards,\nPMS Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send welcome email to %s", user.email)

def send_user_password_changed_email(user, raw_password):
    if not user.email:
        return
    subject = "Your PMS password has been updated"
    message = (
        f"Hi {user.first_name or user.username},\n\n"
        "Your Project Management System password was changed by Admin.\n\n"
        f"Login Email: {user.email}\n"
        f"New Temporary Password: {raw_password}\n\n"
        "Please login for this credentials.\n\n"
        "Regards,\nPMS Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send password change email to %s", user.email)


def send_task_completed_email(task, completed_by):
    owner = task.created_by
    if not owner or not owner.email:
        return
    completed_by_name = completed_by.get_full_name().strip() or completed_by.email
    subject = f"Task Completed: {task.title}"
    message = (
        f"Hi {owner.first_name or owner.username},\n\n"
        "A task created by you is now marked as completed.\n\n"
        f"Task: {task.title}\n"
        f"Completed by: {completed_by_name}\n"
        f"Current Status: {task.status}\n\n"
        "Regards,\nPMS Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[owner.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send task completion email for task %s", task.id)


def send_task_deadline_change_request_email(task, employee, owner, new_deadline=None, reason=""):
    if not owner or not owner.email:
        return
    employee_name = employee.get_full_name().strip() or employee.email
    subject = f"Task Deadline Change Request: {task.title}"
    message = (
        f"Hi {owner.first_name or owner.username},\n\n"
        "An employee has requested a task deadline change.\n\n"
        f"Task: {task.title}\n"
        f"Requested by: {employee_name}\n"
        f"Current Deadline: {task.deadline or 'N/A'}\n"
        f"Requested Deadline: {new_deadline or 'Not provided'}\n"
        f"Reason: {reason or 'No reason provided'}\n\n"
        "Please review and update the deadline if needed.\n\n"
        "Regards,\nPMS Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[owner.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed task deadline request email for task %s", task.id)


def send_project_change_request_email(project, requester, admin_emails, request_type, new_deadline=None, reason=""):
    if not admin_emails:
        return
    requester_name = requester.get_full_name().strip() or requester.email
    subject = f"Project {request_type} Request: {project.name}"
    message = (
        "Hi Admin Team,\n\n"
        f"A BA has submitted a project {request_type.lower()} request.\n\n"
        f"Project: {project.name}\n"
        f"Requested by: {requester_name}\n"
        f"Current Deadline: {project.deadline}\n"
        f"Requested Deadline: {new_deadline or 'Not applicable'}\n"
        f"Reason: {reason or 'No reason provided'}\n\n"
        "Please review and take action.\n\n"
        "Regards,\nPMS Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=admin_emails,
            fail_silently=False,
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




#user view set pagination
@extend_schema(tags=["Admin APIs"])
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("-date_joined")
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in {"update", "partial_update"}:
            return UserUpdateSerializer
        return UserSerializer

    def create(self, request, *args, **kwargs):
        raw_password = request.data.get("password")
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        if raw_password:
            send_user_welcome_email(user, raw_password)
        return api_response(True, "User created successfully.", status.HTTP_201_CREATED, UserSerializer(user).data)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        raw_password = serializer.validated_data.get("password")
        if raw_password:
            send_user_password_changed_email(user, raw_password)
        return api_response(True, "User updated successfully.", status.HTTP_200_OK, UserSerializer(user).data)

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

    def get_permissions(self):
        if self.action == "destroy":
            return [IsAuthenticated(), IsAdmin()]
        if self.action in {"create", "update", "partial_update", "request_deadline_change", "request_delete"}:
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
            admin_emails=[user.email for user in admin_users if user.email],
            request_type="DEADLINE_CHANGE",
            new_deadline=new_deadline,
            reason=reason,
        )
        return api_response(
            True,
            "Project deadline change request sent to admin.",
            status.HTTP_200_OK,
            {"project_id": project.id, "new_deadline": new_deadline, "reason": reason},
        )

    @extend_schema(request=DeleteRequestSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="request-delete")
    def request_delete(self, request, pk=None):
        project = self.get_object()
        if user_role(request.user) != UserProfile.Roles.BA:
            return api_response(False, "Only BA can request project delete.", status.HTTP_403_FORBIDDEN)

        serializer = DeleteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")

        admin_users = User.objects.filter(
            profile__role=UserProfile.Roles.ADMIN,
            profile__status=UserProfile.Status.ACTIVE,
        )
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
            admin_emails=[user.email for user in admin_users if user.email],
            request_type="DELETE",
            reason=reason,
        )
        return api_response(
            True,
            "Project delete request sent to admin.",
            status.HTTP_200_OK,
            {"project_id": project.id, "reason": reason},
        )





#milestone view set pagination
@extend_schema(tags=["BA/Admin APIs"])
class MilestoneViewSet(viewsets.ModelViewSet):
    queryset = Milestone.objects.select_related("project").all().order_by("-created_at")
    serializer_class = MilestoneSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]

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

    def get_queryset(self):
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

    def perform_create(self, serializer):
        project = serializer.validated_data.get("project")
        milestone = serializer.validated_data.get("milestone")
        self._validate_project_milestone_scope(project, milestone)
        self._validate_assignee(serializer.validated_data.get("assigned_to"))
        task = serializer.save(created_by=self.request.user)
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

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def perform_update(self, serializer):
        previous_assignee_id = getattr(serializer.instance, "assigned_to_id", None)
        project = serializer.validated_data.get("project", serializer.instance.project)
        milestone = serializer.validated_data.get("milestone", serializer.instance.milestone)
        self._validate_project_milestone_scope(project, milestone)
        assignee = serializer.validated_data.get("assigned_to", serializer.instance.assigned_to)
        self._validate_assignee(assignee)
        task = serializer.save()
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

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

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
            "Task assigned successfully.",
            status.HTTP_200_OK,
            {"task_id": task.id, "task_name": task.title, "assigned_id": assignee.id, "emp_name": assignee.first_name},
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
        return api_response(True, "Task started.", status.HTTP_200_OK, {"task_id": task.id, "status": task.status})

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        task = self.get_object()
        log = TimeLog.objects.filter(task=task, user=request.user, end_time__isnull=True).last()
        if not log:
            return api_response(False, "No active timer found for this task.", status.HTTP_400_BAD_REQUEST)
        log.stop()
        task.status = Task.Status.PAUSED
        task.save(update_fields=["status", "total_time_spent_seconds"])
        return api_response(True, "Task paused.", status.HTTP_200_OK, {"task_id": task.id, "status": task.status})

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        task = self.get_object()
        log = TimeLog.objects.filter(task=task, user=request.user, end_time__isnull=True).last()
        if not log:
            return api_response(False, "No active timer found for this task.", status.HTTP_400_BAD_REQUEST)
        log.stop()
        return api_response(
            True,
            "Task stopped.",
            status.HTTP_200_OK,
            {
                "task_id": task.id,
                "end_time": log.end_time,
                "duration_seconds": log.duration_seconds,
                "total_time_spent_seconds": task.total_time_spent_seconds,
            },
        )

    @action(detail=True, methods=["get"], url_path="time-logs")
    def time_logs(self, request, pk=None):
        task = self.get_object()
        logs = task.time_logs.all().order_by("-created_at")
        return api_response(True, "Time logs fetched.", status.HTTP_200_OK, TimeLogSerializer(logs, many=True).data)

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        task = self.get_object()
        status_value = request.data.get("status")
        if status_value not in Task.Status.values:
            return api_response(False, "Invalid status.", status.HTTP_400_BAD_REQUEST)
        task.status = status_value
        task.save(update_fields=["status"])
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
        return api_response(
            True, "Task status updated.", status.HTTP_200_OK, {"task_id": task.id, "task_name": task.title, "status": task.status}
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

        return api_response(
            True,
            "Task deadline change request sent.",
            status.HTTP_200_OK,
            {"task_id": task.id, "new_deadline": new_deadline, "reason": reason},
        )





#file attachment view set pagination
@extend_schema(tags=["Common APIs"])
class FileAttachmentViewSet(viewsets.ModelViewSet):
    queryset = FileAttachment.objects.select_related("uploaded_by", "project", "milestone", "task").all().order_by("-created_at")
    serializer_class = FileAttachmentSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        return [IsAuthenticated()]

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
            ba_tasks_qs = Task.objects.filter(created_by=user).select_related("project", "milestone", "assigned_to")
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
                            }
                            for task in tasks_for_employee
                        ],
                    }
                )
            data = {
                "tasks_created": ba_tasks_qs.count(),
                "tasks_completed": ba_tasks_qs.filter(status=Task.Status.COMPLETED).count(),
                "tasks_in_progress": ba_tasks_qs.filter(status=Task.Status.IN_PROGRESS).count(),
                "tasks_delayed": ba_tasks_qs.filter(status=Task.Status.DELAYED).count(),
                "assigned_employees": len(employee_ids),
                "employee_summary": employee_summary,
            }
            return api_response(True, "BA dashboard fetched.", status.HTTP_200_OK, data)
        data = {
            "today_worked_seconds": sum(
                TimeLog.objects.filter(user=user, start_time__date=timezone.localdate()).values_list(
                    "duration_seconds", flat=True
                )
            ),
            "active_task": Task.objects.filter(assigned_to=user, status=Task.Status.IN_PROGRESS).values("id", "title").first(),
            "completed_tasks": Task.objects.filter(assigned_to=user, status=Task.Status.COMPLETED).count(),
        }
        return api_response(True, "Employee dashboard fetched.", status.HTTP_200_OK, data)





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

        return api_response(True, "Admin OTP sent successfully.", status.HTTP_200_OK, {"email": email})


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
        serializer = TaskSerializer(tasks, many=True)
        return api_response(True, "My tasks fetched.", status.HTTP_200_OK, serializer.data)
