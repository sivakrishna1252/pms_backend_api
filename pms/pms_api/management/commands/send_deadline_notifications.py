from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone

from pms_api.models import Milestone, Notification, Project, Task, TimeLog, UserProfile
from pms_api.timer_auto_stop import (
    auto_stop_time_log,
    resolve_auto_stop_phase,
    running_time_logs_queryset,
    should_auto_stop_at_first_pass,
)
from pms_api.views import (
    _admin_mail_recipients,
    _send_styled_email,
    _sync_parent_statuses_for_task,
)


User = get_user_model()


class Command(BaseCommand):
    help = "Send project/milestone/task deadline alert emails and auto-stop open task timers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force-auto-stop",
            action="store_true",
            help="Force final auto-stop for all active timers (ignore time windows).",
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
        grace_hours = int(getattr(settings, "AUTO_STOP_GRACE_HOURS", 1))

        now_local = timezone.localtime()
        is_weekday = now_local.weekday() <= 4  # Monday=0 ... Friday=4
        if not force and not is_weekday:
            return

        phase = resolve_auto_stop_phase(
            now_local,
            cutoff_hour=cutoff_hour,
            cutoff_minute=cutoff_minute,
            grace_hours=grace_hours,
            force=force,
        )
        if phase is None:
            return

        cutoff_dt = now_local.replace(
            hour=cutoff_hour, minute=cutoff_minute, second=0, microsecond=0
        )
        final_hour = (cutoff_dt + timedelta(hours=grace_hours)).strftime("%H:%M")

        active_logs = list(running_time_logs_queryset())
        if not active_logs:
            return

        stopped_by_user = {}
        deferred_count = 0

        for log in active_logs:
            if phase == "first" and not should_auto_stop_at_first_pass(
                log, cutoff_dt, grace_hours
            ):
                deferred_count += 1
                continue

            auto_stop_time_log(log, sync_task_status=True)
            _sync_parent_statuses_for_task(log.task)

            user_email = getattr(log.user, "email", None)
            if not user_email:
                continue
            stopped_by_user.setdefault(
                user_email,
                {"name": log.user.first_name or log.user.username, "tasks": []},
            )
            stopped_by_user[user_email]["tasks"].append(log.task.title)

        if deferred_count and phase == "first":
            self.stdout.write(
                self.style.WARNING(
                    f"Deferred {deferred_count} running timer(s) with recent activity near "
                    f"{cutoff_hour:02d}:{cutoff_minute:02d}; final auto-stop at {final_hour} if still running."
                )
            )

        if not stopped_by_user:
            return

        for email, payload in stopped_by_user.items():
            task_lines = ", ".join(payload["tasks"]) or "N/A"
            if phase == "first":
                subject = f"PMS Timer Auto-stopped at {cutoff_hour:02d}:{cutoff_minute:02d}"
                intro = (
                    f"You left task timer(s) running (forgot to stop or pause) after "
                    f"{cutoff_hour:02d}:{cutoff_minute:02d} (Mon–Fri). PMS auto-stopped them."
                )
                reminder = (
                    "Paused tasks were not changed. If you are still working, start the timer again. "
                    f"Timers with recent activity near {cutoff_hour:02d}:{cutoff_minute:02d} are checked again at {final_hour}."
                )
            else:
                subject = f"PMS Timer Auto-stopped at {final_hour}"
                intro = (
                    f"You still had running task timer(s) at the {final_hour} check "
                    f"(Mon–Fri), so PMS auto-stopped them."
                )
                reminder = (
                    "Please stop or pause your timer when you finish work. "
                    "Paused tasks are never auto-stopped."
                )

            _send_styled_email(
                subject=subject,
                recipient_list=[email],
                greeting=f"Hi {payload['name']},",
                intro_text=intro,
                detail_rows=[
                    ("Auto-stopped Tasks", task_lines),
                    ("Reminder", reminder),
                ],
            )

    def _send_project_deadline_alerts(self, today, tomorrow):
        ba_emails = list(
            User.objects.filter(
                profile__role=UserProfile.Roles.BA,
                profile__status=UserProfile.Status.ACTIVE,
            ).values_list("email", flat=True)
        )
        recipients = [email for email in (_admin_mail_recipients() + ba_emails) if email]

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
                recipients=recipients,
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
                recipients=recipients,
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
