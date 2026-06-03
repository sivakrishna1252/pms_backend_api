"""Recompute project and milestone statuses from tasks and work-tracking progress."""

from django.core.management.base import BaseCommand

from pms_api.models import Project
from pms_api.views import sync_parent_statuses_for_project


class Command(BaseCommand):
    help = (
        "Align project and milestone statuses with task states and work progress "
        "(fixes PLANNED/NOT_STARTED when progress > 0)."
    )

    def handle(self, *args, **options):
        project_ids = list(Project.objects.values_list("id", flat=True))
        for project_id in project_ids:
            sync_parent_statuses_for_project(project_id)
        self.stdout.write(
            self.style.SUCCESS(f"Synced statuses for {len(project_ids)} project(s).")
        )
