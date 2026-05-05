import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


# Repo-root files copied into MEDIA_ROOT so /media/project_docs/... matches FileField layout.
BUNDLED_DOCS = (
    ("API_MASTER_GUIDE.md", "project_docs/API_MASTER_GUIDE.md"),
)


class Command(BaseCommand):
    help = "Copy bundled documentation files from the project tree into MEDIA_ROOT (idempotent)."

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR)
        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)

        for src_name, dest_relative in BUNDLED_DOCS:
            src = base / src_name
            dest = media_root / dest_relative
            if not src.is_file():
                self.stdout.write(self.style.WARNING(f"Skip missing source: {src}"))
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            self.stdout.write(self.style.SUCCESS(f"Synced {src_name} -> {dest_relative}"))
