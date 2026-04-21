from datetime import time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone

from pms_api.models import Milestone, Project, Task, TimeLog, UserProfile


User = get_user_model()


class Command(BaseCommand):
    help = "Send project/milestone/task deadline alert emails."

    def handle(self, *args, **options):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)

        self._auto_stop_active_timers_at_8pm()
        self._send_project_deadline_alerts(today, tomorrow)
        self._send_milestone_overdue_alerts(today)
        self._send_task_overdue_alerts(today)
        self.stdout.write(self.style.SUCCESS("Deadline notifications job completed."))

    def _send_email(self, subject, message, recipients):
        recipients = [email for email in recipients if email]
        if not recipients:
            return
        send_mail(
            subject=subject,
            message=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=list(set(recipients)),
            fail_silently=False,
        )

    def _auto_stop_active_timers_at_8pm(self):
        now_local = timezone.localtime()
        if now_local.time() < time(hour=20, minute=0):
            return

        active_logs = TimeLog.objects.select_related("task", "user").filter(end_time__isnull=True)
        if not active_logs.exists():
            return

        stopped_by_user = {}
        for log in active_logs:
            log.stop(source=TimeLog.Source.AUTO_STOP_8PM)
            user_email = getattr(log.user, "email", None)
            if not user_email:
                continue
            stopped_by_user.setdefault(user_email, {"name": log.user.first_name or log.user.username, "tasks": []})
            stopped_by_user[user_email]["tasks"].append(log.task.title)

        for email, payload in stopped_by_user.items():
            task_lines = "\n".join(f"- {title}" for title in payload["tasks"]) or "- N/A"
            self._send_email(
                subject="PMS Timer Auto-stopped at 8:00 PM",
                message=(
                    f"Hi {payload['name']},\n\n"
                    "You had active task timer(s) after 8:00 PM, so PMS auto-stopped them.\n\n"
                    "Auto-stopped tasks:\n"
                    f"{task_lines}\n\n"
                    "Please remember to stop your timer when work is finished.\n\n"
                    "Regards,\nPMS Team"
                ),
                recipients=[email],
            )

    def _send_project_deadline_alerts(self, today, tomorrow):
        admins_and_bas = User.objects.filter(
            profile__role__in=[UserProfile.Roles.ADMIN, UserProfile.Roles.BA],
            profile__status=UserProfile.Status.ACTIVE,
        ).values_list("email", flat=True)

        due_tomorrow = Project.objects.filter(
            deadline=tomorrow
        ).exclude(status__in=[Project.Status.COMPLETED, Project.Status.ARCHIVED])
        for project in due_tomorrow:
            self._send_email(
                subject=f"Project Deadline Tomorrow: {project.name}",
                message=(
                    f"Project '{project.name}' is due tomorrow ({project.deadline}) and is not completed yet.\n"
                    f"Current status: {project.status}\n"
                    "Please review and take action."
                ),
                recipients=admins_and_bas,
            )

        overdue_projects = Project.objects.filter(deadline__lt=today).exclude(
            status__in=[Project.Status.COMPLETED, Project.Status.ARCHIVED]
        )
        for project in overdue_projects:
            if project.status != Project.Status.DELAYED:
                project.status = Project.Status.DELAYED
                project.save(update_fields=["status"])
            self._send_email(
                subject=f"Project Delayed: {project.name}",
                message=(
                    f"Project '{project.name}' crossed deadline ({project.deadline}) and is still not completed.\n"
                    f"Current status changed to: {project.status}\n"
                    "Please review and take action."
                ),
                recipients=admins_and_bas,
            )

    def _send_milestone_overdue_alerts(self, today):
        overdue_milestones = Milestone.objects.select_related("created_by", "project").filter(end_date__lt=today).exclude(
            status=Milestone.Status.COMPLETED
        )
        for milestone in overdue_milestones:
            if milestone.status != Milestone.Status.DELAYED:
                milestone.status = Milestone.Status.DELAYED
                milestone.save(update_fields=["status"])
            owner = milestone.created_by
            self._send_email(
                subject=f"Milestone Delayed: {milestone.name}",
                message=(
                    f"Milestone '{milestone.name}' in project '{milestone.project.name}' crossed end date "
                    f"({milestone.end_date}) and is still not completed.\n"
                    f"Current status changed to: {milestone.status}\n"
                    "Please review and update milestone plan."
                ),
                recipients=[getattr(owner, "email", None)],
            )

    def _send_task_overdue_alerts(self, today):
        overdue_tasks = Task.objects.select_related("created_by", "assigned_to", "project").filter(
            deadline__isnull=False,
            deadline__lt=today,
        ).exclude(status=Task.Status.COMPLETED)
        for task in overdue_tasks:
            if task.status != Task.Status.DELAYED:
                task.status = Task.Status.DELAYED
                task.save(update_fields=["status"])
            owner = task.created_by
            self._send_email(
                subject=f"Task Delayed: {task.title}",
                message=(
                    f"Task '{task.title}' in project '{task.project.name}' crossed deadline ({task.deadline}) "
                    "and is still not completed.\n"
                    f"Current status changed to: {task.status}\n"
                    f"Assigned employee: {getattr(task.assigned_to, 'email', 'N/A')}\n"
                    "Please review and take action."
                ),
                recipients=[getattr(owner, "email", None)],
            )
