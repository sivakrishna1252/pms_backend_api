"""Auto-stop all running task timers at 8 PM (Mon–Sat) and email assignees."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from pms_api.timer_auto_stop import is_past_auto_stop_cutoff, run_evening_auto_stop_if_due
from pms_api.views import _sync_parent_statuses_for_task


class Command(BaseCommand):
    help = (
        "Mon–Sat 8 PM job: stop every running task timer once, email assignees, "
        "then leave late-started tasks alone until the next 8 PM run. "
        "Paused tasks are not touched."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even outside the 8 PM window or on Sunday (testing).",
        )

    def handle(self, *args, **options):
        force = bool(options.get("force"))
        now_local = timezone.localtime()
        cutoff_hour = int(getattr(settings, "AUTO_STOP_CUTOFF_HOUR", 20))
        cutoff_minute = int(getattr(settings, "AUTO_STOP_CUTOFF_MINUTE", 0))

        if not force:
            if now_local.weekday() > 5:
                self.stdout.write(self.style.WARNING("Skipped: Sunday (Mon–Sat only)."))
                return
            if not is_past_auto_stop_cutoff(now_local):
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipped: before {cutoff_hour:02d}:{cutoff_minute:02d} local time "
                        f"({settings.TIME_ZONE})."
                    )
                )
                return

        stopped_by_user = run_evening_auto_stop_if_due(
            force=force,
            notify=True,
            on_task_sync=_sync_parent_statuses_for_task,
        )

        if not stopped_by_user:
            self.stdout.write(
                self.style.SUCCESS(
                    "No running task timers at 8 PM — nothing to stop, no email sent."
                )
            )
            return

        total = sum(len(p["tasks"]) for p in stopped_by_user.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"Auto-stopped {total} running timer(s) for {len(stopped_by_user)} employee(s). "
                f"Notification email sent."
            )
        )
