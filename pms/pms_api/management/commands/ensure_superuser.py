import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "If DJANGO_SUPERUSER_USERNAME and DJANGO_SUPERUSER_PASSWORD are set, ensure a "
        "superuser with that username exists (for production bootstrap only)."
    )

    def handle(self, *args, **options):
        username = (os.environ.get("DJANGO_SUPERUSER_USERNAME") or "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD") or ""
        email = (os.environ.get("DJANGO_SUPERUSER_EMAIL") or "").strip()

        if not username or not password:
            self.stdout.write(
                "Skipping ensure_superuser (set DJANGO_SUPERUSER_USERNAME "
                "and DJANGO_SUPERUSER_PASSWORD in the server env)."
            )
            return

        User = get_user_model()

        existing = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
        if existing:
            self.stdout.write(
                self.style.WARNING(
                    f"User {username!r} already exists; not changing password (use changepassword or shell)."
                )
            )
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created superuser {username!r}."))
