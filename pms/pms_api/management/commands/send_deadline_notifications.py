from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone

from pms_api.models import Milestone, Notification, Project, Task, UserProfile
from pms_api.views import (
    _admin_mail_recipients,
    _send_styled_email,
)


User = get_user_model()


class Command(BaseCommand):
    help = "Send project/milestone/task deadline alert emails (run daily, e.g. morning)."

    def handle(self, *args, **options):
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)

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
