from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
import logging
from rest_framework import status, viewsets
from rest_framework.decorators import action
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
    FileAttachmentSerializer,
    MilestoneSerializer,
    NotificationSerializer,
    ProjectSerializer,
    TaskSerializer,
    TimeLogSerializer,
    UserCreateSerializer,
    UserSerializer,
)


#all api response function
User = get_user_model()
logger = logging.getLogger(__name__)

def api_response(success, message, code, data=None):
    return Response(
        {"success": success, "message": message, "code": code, "data": data},
        status=code,
    )

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






#login api view
class LoginAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AuthLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        payload = AuthResponseSerializer.build(user)
        return api_response(True, "Login successful.", status.HTTP_200_OK, payload)

class RefreshAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return api_response(True, "Token refreshed successfully.", status.HTTP_200_OK, serializer.validated_data)




#me api view
class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        UserProfile.objects.get_or_create(user=request.user)
        return api_response(True, "User profile fetched.", status.HTTP_200_OK, UserSerializer(request.user).data)




#user view set pagination
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("-date_joined")
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        return UserSerializer

    def create(self, request, *args, **kwargs):
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return api_response(True, "User created successfully.", status.HTTP_201_CREATED, UserSerializer(user).data)




#project view set pagination
class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all().order_by("-created_at")
    serializer_class = ProjectSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action == "destroy":
            return [IsAuthenticated(), IsAdmin()]
        if self.action in {"create", "update", "partial_update"}:
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





#milestone view set pagination
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
        if self.action in {"create", "update", "partial_update", "destroy", "assign"}:
            return [IsAuthenticated(), IsAdminOrBA()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
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

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        task = self.get_object()
        user_id = request.data.get("user_id")
        employee = User.objects.filter(
            id=user_id, profile__role=UserProfile.Roles.EMPLOYEE, profile__status=UserProfile.Status.ACTIVE
        ).first()
        if not employee:
            return api_response(False, "Employee not found.", status.HTTP_400_BAD_REQUEST)
        task.assigned_to = employee
        task.save(update_fields=["assigned_to"])
        Notification.objects.create(
            user=employee,
            type="TASK_ASSIGNED",
            title="New Task Assigned",
            message=f"Task '{task.title}' was assigned to you.",
            ref_type=Notification.RefType.TASK,
            ref_id=task.id,
        )
        send_task_assignment_email(employee, task)
        return api_response(
            True,
            "Task assigned successfully.",
            status.HTTP_200_OK,
            {"task_id": task.id, "task_name": task.title, "assigned_id": employee.id, "emp_name": employee.first_name},
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
        return api_response(
            True, "Task status updated.", status.HTTP_200_OK, {"task_id": task.id, "task_name": task.title, "status": task.status}
        )





#file attachment view set pagination
class FileAttachmentViewSet(viewsets.ModelViewSet):
    queryset = FileAttachment.objects.select_related("uploaded_by").all().order_by("-created_at")
    serializer_class = FileAttachmentSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        upload = self.request.FILES.get("file")
        serializer.save(
            uploaded_by=self.request.user,
            mime_type=getattr(upload, "content_type", ""),
            size_bytes=getattr(upload, "size", 0),
        )





#notification view set pagination
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
class DashboardAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user_role(user) == UserProfile.Roles.ADMIN:
            data = {
                "users_count": User.objects.count(),
                "projects_count": Project.objects.count(),
                "tasks_count": Task.objects.count(),
                "active_timers": TimeLog.objects.filter(end_time__isnull=True).count(),
            }
            return api_response(True, "Admin dashboard fetched.", status.HTTP_200_OK, data)
        if user_role(user) == UserProfile.Roles.BA:
            data = {
                "tasks_created": Task.objects.filter(created_by=user).count(),
                "tasks_completed": Task.objects.filter(created_by=user, status=Task.Status.COMPLETED).count(),
                "assigned_employees": User.objects.filter(tasks_assigned__created_by=user).distinct().count(),
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





#my tasks api view
class MyTasksAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tasks = Task.objects.filter(assigned_to=request.user).order_by("-created_at")
        serializer = TaskSerializer(tasks, many=True)
        return api_response(True, "My tasks fetched.", status.HTTP_200_OK, serializer.data)
