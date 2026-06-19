"""Auto-stop all running task timers at 8 PM (weekdays) and email assignees."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from pms_api.timer_auto_stop import auto_stop_all_running_timers
from pms_api.views import _send_styled_email, _sync_parent_statuses_for_task


class Command(BaseCommand):
    help = (
        "Weekday 8 PM job: stop every running task timer (IN_PROGRESS), email assignees, "
        "and remind them not to forget again. Paused tasks are not touched."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even outside the 8 PM window or on weekends (testing).",
        )

    def handle(self, *args, **options):
        force = bool(options.get("force"))
        now_local = timezone.localtime()
        cutoff_hour = int(getattr(settings, "AUTO_STOP_CUTOFF_HOUR", 20))
        cutoff_minute = int(getattr(settings, "AUTO_STOP_CUTOFF_MINUTE", 0))

        if not force:
            if now_local.weekday() > 4:
                self.stdout.write(self.style.WARNING("Skipped: weekend (Mon–Fri only)."))
                return
            if now_local.hour < cutoff_hour or (
                now_local.hour == cutoff_hour and now_local.minute < cutoff_minute
            ):
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipped: before {cutoff_hour:02d}:{cutoff_minute:02d} local time."
                    )
                )
                return

        stopped_by_user = auto_stop_all_running_timers(
            sync_task_status=True,
            on_task_sync=_sync_parent_statuses_for_task,
        )

        if not stopped_by_user:
            self.stdout.write(self.style.SUCCESS("No running task timers to stop."))
            return

        cutoff_label = f"{cutoff_hour:02d}:{cutoff_minute:02d}"
        for email, payload in stopped_by_user.items():
            task_lines = ", ".join(payload["tasks"]) or "N/A"
            _send_styled_email(
                subject=f"PMS: Task timer auto-stopped at {cutoff_label}",
                recipient_list=[email],
                greeting=f"Hi {payload['name']},",
                intro_text=(
                    f"You forgot to stop your task timer(s) before {cutoff_label}. "
                    "PMS has auto-stopped them for you."
                ),
                detail_rows=[
                    ("Auto-stopped tasks", task_lines),
                    (
                        "Reminder",
                        "Please stop or pause your timer when you finish work. "
                        "Do not forget again — paused tasks are never auto-stopped.",
                    ),
                ],
            )

        total = sum(len(p["tasks"]) for p in stopped_by_user.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"Auto-stopped {total} running timer(s) for {len(stopped_by_user)} employee(s)."
            )
        )
