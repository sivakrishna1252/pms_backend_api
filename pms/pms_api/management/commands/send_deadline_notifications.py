from datetime import time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone

from pms_api.models import Milestone, Notification, Project, Task, TimeLog, UserProfile
from pms_api.views import _send_styled_email


User = get_user_model()


class Command(BaseCommand):
    help = "Send project/milestone/task deadline alert emails."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force-auto-stop",
            action="store_true",
            help="Force auto-stop active timers immediately (ignore cutoff time).",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)

        self._auto_stop_active_timers(force=bool(options.get("force_auto_stop")))
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

    def _auto_stop_active_timers(self, force=False):
        cutoff_hour = int(getattr(settings, "AUTO_STOP_CUTOFF_HOUR", 20))
        cutoff_minute = int(getattr(settings, "AUTO_STOP_CUTOFF_MINUTE", 0))
        cutoff_time = time(hour=cutoff_hour, minute=cutoff_minute)

        now_local = timezone.localtime()
        is_weekday = now_local.weekday() <= 4  # Monday=0 ... Friday=4
        if not force and not is_weekday:
            return
        if not force and now_local.time() < cutoff_time:
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
            task_lines = ", ".join(payload["tasks"]) or "N/A"
            _send_styled_email(
                subject=f"PMS Timer Auto-stopped at {cutoff_hour:02d}:{cutoff_minute:02d}",
                recipient_list=[email],
                greeting=f"Hi {payload['name']},",
                intro_text=(
                    f"You had active task timer(s) after {cutoff_hour:02d}:{cutoff_minute:02d} "
                    "(Mon-Fri), so PMS auto-stopped them."
                ),
                detail_rows=[
                    ("Auto-stopped Tasks", task_lines),
                    ("Reminder", "Please remember to stop your timer when work is finished."),
                ],
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
            transitioned_to_delayed = milestone.status != Milestone.Status.DELAYED
            if transitioned_to_delayed:
                milestone.status = Milestone.Status.DELAYED
                milestone.save(update_fields=["status"])
            owner = milestone.created_by
            owner_email = getattr(owner, "email", None)
            if not transitioned_to_delayed or not owner_email:
                continue

            Notification.objects.create(
                user=owner,
                type="MILESTONE_DELAYED",
                title="Milestone Delayed",
                message=(
                    f"Milestone '{milestone.name}' in project '{milestone.project.name}' is delayed."
                ),
                ref_type=Notification.RefType.MILESTONE,
                ref_id=milestone.id,
            )
            _send_styled_email(
                subject=f"Milestone Delayed: {milestone.name}",
                recipient_list=[owner_email],
                greeting=f"Hi {owner.first_name or owner.username},",
                intro_text=(
                    f"Your milestone '{milestone.name}' in project '{milestone.project.name}' "
                    f"crossed its end date and is marked as Delayed."
                ),
                detail_rows=[
                    ("Milestone", milestone.name),
                    ("Project", milestone.project.name),
                    ("End Date", str(milestone.end_date)),
                    ("Current Status", milestone.status),
                ],
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
