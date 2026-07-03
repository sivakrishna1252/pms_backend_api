import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


class Command(BaseCommand):
    help = (
        "If DJANGO_SUPERUSER_USERNAME and DJANGO_SUPERUSER_PASSWORD are set, ensure a "
        "staff/superuser exists. Creates the user or, if the user exists without admin "
        "rights, can promote them when DJANGO_SUPERUSER_PROMOTE_EXISTING=true."
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
        lookup = {User.USERNAME_FIELD: username}

        existing = User.objects.filter(**lookup).first()
        if existing:
            if getattr(existing, "is_staff", False):
                if _truthy_env("DJANGO_SUPERUSER_PROMOTE_EXISTING"):
                    existing.is_staff = True
                    existing.is_superuser = True
                    existing.set_password(password)
                    if email:
                        setattr(existing, "email", email)
                    update_fields = ["is_staff", "is_superuser", "password"]
                    if email:
                        update_fields.append("email")
                    existing.save(update_fields=update_fields)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Updated existing staff/superuser {username!r} "
                            f"(DJANGO_SUPERUSER_PROMOTE_EXISTING=true)."
                        )
                    )
                    self._ensure_portal_admin_profile(username)
                    return

                self.stdout.write(
                    self.style.WARNING(
                        f"User {username!r} already exists with is_staff=True; not changing "
                        f"password. If /admin still rejects you, the password may be wrong — run "
                        f"`python manage.py changepassword {username}` on the server container."
                    )
                )
                return

            if not _truthy_env("DJANGO_SUPERUSER_PROMOTE_EXISTING"):
                self.stdout.write(
                    self.style.ERROR(
                        f"User {username!r} exists but is NOT staff; /admin login will fail. "
                        f"Set DJANGO_SUPERUSER_PROMOTE_EXISTING=true (with matching USERNAME/PASSWORD) "
                        f"and redeploy, or delete this user and run createsuperuser on the server."
                    )
                )
                return

            existing.is_staff = True
            existing.is_superuser = True
            existing.set_password(password)
            if email:
                setattr(existing, "email", email)
            uf = ["is_staff", "is_superuser", "password"]
            if email:
                uf.append("email")
            existing.save(update_fields=uf)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Promoted existing user {username!r} to staff/superuser "
                    f"(DJANGO_SUPERUSER_PROMOTE_EXISTING=true)."
                )
            )
            self._ensure_portal_admin_profile(username)
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created superuser {username!r}."))
        self._ensure_portal_admin_profile(username)

    def _ensure_portal_admin_profile(self, username: str) -> None:
        from pms_api.models import UserProfile

        User = get_user_model()
        user = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
        if not user:
            return
        profile, _ = UserProfile.objects.get_or_create(user=user)
        updates = []
        if not profile.password_set:
            profile.password_set = True
            updates.append("password_set")
        if profile.role != UserProfile.Roles.ADMIN:
            profile.role = UserProfile.Roles.ADMIN
            updates.append("role")
        if updates:
            profile.save(update_fields=updates)
