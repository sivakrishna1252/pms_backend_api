from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from pms_api.models import UserProfile

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Create the first portal ADMIN user (JWT /auth/login + dashboard). "
        "Matches API_DOCUMENTATION.md sample unless you pass --email/--password."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="admin@apparatus.solutions",
            help="Login email (also stored as username).",
        )
        parser.add_argument(
            "--password",
            default="Admin@1234",
            help="Initial password.",
        )
        parser.add_argument(
            "--force-password",
            action="store_true",
            help="If the user already exists, reset password and ensure ADMIN profile.",
        )

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        password = options["password"]
        force_pw = options["force_password"]

        user = (
            User.objects.filter(username__iexact=email).first()
            or User.objects.filter(email__iexact=email).first()
        )

        if user and not force_pw:
            self.stdout.write(
                self.style.WARNING(
                    f"User already exists ({user.username!r}). "
                    f"Pass --force-password to reset password and enforce ADMIN profile."
                )
            )
            return

        with transaction.atomic():
            if user:
                user.set_password(password)
                if not user.email:
                    user.email = email
                if user.username.lower() != email:
                    user.username = email
                user.save()
                profile, _ = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "role": UserProfile.Roles.ADMIN,
                        "status": UserProfile.Status.ACTIVE,
                    },
                )
                if profile.role != UserProfile.Roles.ADMIN or profile.status != UserProfile.Status.ACTIVE:
                    profile.role = UserProfile.Roles.ADMIN
                    profile.status = UserProfile.Status.ACTIVE
                    profile.save()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated dashboard admin {email!r} (password reset, ADMIN profile ensured)."
                    )
                )
                return

            user = User(
                username=email,
                email=email,
                first_name="Admin",
                last_name="User",
            )
            user.set_password(password)
            user.save()
            UserProfile.objects.create(
                user=user,
                role=UserProfile.Roles.ADMIN,
                status=UserProfile.Status.ACTIVE,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created dashboard admin {email!r}. Login via POST /api/v1/auth/login with this email and password."
                )
            )
