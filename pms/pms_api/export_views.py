"""Admin/BA export endpoints for projects, milestones, and tasks."""

from __future__ import annotations

from django.db.models import Count
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .export_utils import (
    EXPORT_FORMAT_CSV,
    EXPORT_FORMAT_EXCEL,
    EXPORT_FORMAT_PDF,
    export_file_response,
    format_display_date,
    matches_progress_band,
    milestone_status_label,
    project_status_label,
    task_status_label,
    ui_project_status_to_api,
)
from .models import Milestone, Project, Task, TimeLog, UserProfile
from .permissions import IsAdminOrBA, user_role
from .progress import milestone_progress_data, project_progress_data, task_progress_percent
from .timer_state import assignee_timer_state


def _employee_display_name(user) -> str:
    if user is None:
        return ""
    return user.get_full_name().strip() or user.email


def _task_progress_label(task: Task) -> str:
    today = timezone.localdate()
    overdue = (
        task.deadline is not None
        and task.deadline < today
        and task.status != Task.Status.COMPLETED
    )
    if overdue or task.status == Task.Status.DELAYED:
        return "Delayed"
    if task.status == Task.Status.COMPLETED:
        return "Complete"

    timer = assignee_timer_state(task)
    if timer == "STARTED":
        return "Running"
    if timer == "PAUSED":
        return "Paused"
    if timer == "AUTO_STOPPED":
        return "Auto stop"
    if timer == "STOPPED":
        return "Stopped"

    if not timer and task.assigned_to_id:
        last = (
            TimeLog.objects.filter(
                task=task,
                user_id=task.assigned_to_id,
                end_time__isnull=False,
            )
            .order_by("-end_time")
            .first()
        )
        if last and last.source == TimeLog.Source.AUTO_STOP_8PM and task.status != Task.Status.NOT_STARTED:
            return "Auto stop"

    if task.status == Task.Status.IN_PROGRESS:
        return "Stopped"
    if task.status == Task.Status.BLOCKED:
        return "Stopped"
    if task.status == Task.Status.NOT_STARTED:
        return "Not Started"
    return "Not Started"


def _projects_queryset(request):
    queryset = Project.objects.select_related("created_by").annotate(
        files_attachment_count=Count("files", distinct=True),
    ).order_by("-created_at")
    if user_role(request.user) == UserProfile.Roles.EMPLOYEE:
        return queryset.filter(tasks__assigned_to=request.user).distinct()
    return queryset


def _milestones_queryset(request):
    queryset = Milestone.objects.select_related("project", "created_by").order_by("-created_at")
    if user_role(request.user) == UserProfile.Roles.EMPLOYEE:
        return queryset.filter(tasks__assigned_to=request.user).distinct()
    return queryset


def _tasks_queryset(request):
    from .views import apply_automatic_task_status_rules

    apply_automatic_task_status_rules()
    queryset = Task.objects.select_related(
        "project",
        "milestone",
        "assigned_to",
        "created_by",
    ).order_by("-created_at")
    if user_role(request.user) == UserProfile.Roles.EMPLOYEE:
        return queryset.filter(assigned_to=request.user)
    return queryset


def _parse_filters(request):
    return {
        "project_id": request.query_params.get("project_id", "").strip(),
        "milestone_id": request.query_params.get("milestone_id", "").strip(),
        "task_id": request.query_params.get("task_id", "").strip(),
        "status": request.query_params.get("status", "").strip(),
        "progress_band": request.query_params.get("progress_band", "").strip(),
        "progress": request.query_params.get("progress", "").strip(),
        "employee_id": request.query_params.get("employee_id", "").strip(),
        "employee_name": request.query_params.get("employee_name", "").strip(),
    }


class _BaseExportAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrBA]
    entity_slug = "data"
    report_title = "Export"
    column_keys: list[str] = []
    column_labels: list[str] = []

    def build_rows(self, request, filters):  # pragma: no cover - implemented per entity
        raise NotImplementedError

    @extend_schema(
        tags=["Admin Exports"],
        parameters=[
            OpenApiParameter("export", OpenApiTypes.STR, description="csv, excel, or pdf"),
            OpenApiParameter("project_id", OpenApiTypes.INT),
            OpenApiParameter("milestone_id", OpenApiTypes.INT),
            OpenApiParameter("task_id", OpenApiTypes.INT),
            OpenApiParameter("status", OpenApiTypes.STR, description="UI status label"),
            OpenApiParameter("progress_band", OpenApiTypes.STR, description="0-50, 51-75, or 76-100"),
            OpenApiParameter("progress", OpenApiTypes.STR, description="Task progress label"),
            OpenApiParameter("employee_id", OpenApiTypes.INT),
            OpenApiParameter("employee_name", OpenApiTypes.STR),
        ],
    )
    def get(self, request):
        filters = _parse_filters(request)
        rows = self.build_rows(request, filters)
        export_format = request.query_params.get("export", "").lower()

        if export_format in {EXPORT_FORMAT_CSV, EXPORT_FORMAT_EXCEL, EXPORT_FORMAT_PDF}:
            stamp = timezone.localdate().isoformat()
            response = export_file_response(
                export_format,
                f"{self.entity_slug}_{stamp}",
                self.report_title,
                self.column_keys,
                self.column_labels,
                rows,
            )
            if response is not None:
                return response

        return Response(
            {
                "filters": filters,
                "report": {
                    "title": self.report_title,
                    "columns": self.column_keys,
                    "column_labels": dict(zip(self.column_keys, self.column_labels, strict=False)),
                    "rows": rows,
                    "row_count": len(rows),
                    "export_formats": [EXPORT_FORMAT_PDF, EXPORT_FORMAT_EXCEL, EXPORT_FORMAT_CSV],
                },
            }
        )


class ProjectsExportAPIView(_BaseExportAPIView):
    entity_slug = "projects"
    report_title = "Project Management Export"
    column_keys = [
        "id",
        "name",
        "description",
        "start_date",
        "deadline",
        "status",
        "progress_percent",
        "created_by",
    ]
    column_labels = [
        "ID",
        "Project Name",
        "Description",
        "Start Date",
        "Deadline",
        "Status",
        "Progress %",
        "Created By",
    ]

    def build_rows(self, request, filters):
        queryset = _projects_queryset(request)
        if filters["project_id"]:
            queryset = queryset.filter(id=filters["project_id"])

        status_filter = filters["status"]
        api_status = ui_project_status_to_api(status_filter) if status_filter else None
        if api_status:
            if api_status == "COMPLETED":
                queryset = queryset.filter(status__in=[Project.Status.COMPLETED, Project.Status.ARCHIVED])
            else:
                queryset = queryset.filter(status=api_status)

        rows = []
        for project in queryset:
            progress = project_progress_data(project).get("progress_percent")
            if progress is None:
                progress_value = 0
            else:
                progress_value = progress
            if not matches_progress_band(progress_value, filters["progress_band"]):
                continue
            rows.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "description": project.description or "",
                    "start_date": format_display_date(project.start_date),
                    "deadline": format_display_date(project.deadline),
                    "status": project_status_label(project.status),
                    "progress_percent": progress_value,
                    "created_by": _employee_display_name(project.created_by),
                }
            )
        return rows


class MilestonesExportAPIView(_BaseExportAPIView):
    entity_slug = "milestones"
    report_title = "Milestone Management Export"
    column_keys = [
        "id",
        "milestone_no",
        "project_name",
        "name",
        "description",
        "start_date",
        "end_date",
        "status",
        "progress_percent",
        "created_by",
    ]
    column_labels = [
        "ID",
        "Milestone #",
        "Project",
        "Milestone Name",
        "Description",
        "Start Date",
        "End Date",
        "Status",
        "Progress %",
        "Created By",
    ]

    def build_rows(self, request, filters):
        queryset = _milestones_queryset(request)
        if filters["project_id"]:
            queryset = queryset.filter(project_id=filters["project_id"])
        if filters["milestone_id"]:
            queryset = queryset.filter(id=filters["milestone_id"])

        rows = []
        for milestone in queryset:
            progress = milestone_progress_data(milestone).get("progress_percent")
            progress_value = progress if progress is not None else 0
            if not matches_progress_band(progress_value, filters["progress_band"]):
                continue
            rows.append(
                {
                    "id": milestone.id,
                    "milestone_no": milestone.milestone_no,
                    "project_name": milestone.project.name,
                    "name": milestone.name,
                    "description": milestone.description or "",
                    "start_date": format_display_date(milestone.start_date),
                    "end_date": format_display_date(milestone.end_date),
                    "status": milestone_status_label(milestone.status),
                    "progress_percent": progress_value,
                    "created_by": _employee_display_name(milestone.created_by),
                }
            )
        return rows


class TasksExportAPIView(_BaseExportAPIView):
    entity_slug = "tasks"
    report_title = "Tasks Export"
    column_keys = [
        "id",
        "title",
        "project_name",
        "milestone_name",
        "assigned_to",
        "status",
        "progress",
        "progress_percent",
        "start_date",
        "deadline",
    ]
    column_labels = [
        "ID",
        "Task",
        "Project",
        "Milestone",
        "Assigned To",
        "Status",
        "Progress",
        "Progress %",
        "Created",
        "Deadline",
    ]

    def build_rows(self, request, filters):
        queryset = _tasks_queryset(request)
        if filters["project_id"]:
            queryset = queryset.filter(project_id=filters["project_id"])
        if filters["milestone_id"]:
            queryset = queryset.filter(milestone_id=filters["milestone_id"])
        if filters["task_id"]:
            queryset = queryset.filter(id=filters["task_id"])
        if filters["employee_id"]:
            queryset = queryset.filter(assigned_to_id=filters["employee_id"])

        rows = []
        for task in queryset:
            assignee_name = _employee_display_name(task.assigned_to)
            if filters["employee_name"]:
                if assignee_name.strip().lower() != filters["employee_name"].strip().lower():
                    continue

            progress_label = _task_progress_label(task)
            if filters["progress"] and progress_label != filters["progress"]:
                continue

            progress_value = task_progress_percent(task)
            rows.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "project_name": task.project.name if task.project_id else "",
                    "milestone_name": task.milestone.name if task.milestone_id else "",
                    "assigned_to": assignee_name,
                    "status": task_status_label(task.status),
                    "progress": progress_label,
                    "progress_percent": progress_value,
                    "start_date": format_display_date(
                        task.created_at.date() if task.created_at else None
                    ),
                    "deadline": format_display_date(task.deadline),
                }
            )
        return rows
