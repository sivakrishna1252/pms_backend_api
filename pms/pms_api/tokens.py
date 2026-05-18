from rest_framework_simplejwt.tokens import RefreshToken

from .models import UserProfile


class PMSRefreshToken(RefreshToken):
    """Embed profile role and Django staff flags for downstream services (e.g. attendance)."""

    @classmethod
    def for_user(cls, user):
        token = super().for_user(user)
        profile = getattr(user, "profile", None)
        if profile is None:
            profile, _ = UserProfile.objects.get_or_create(user=user)
        role = getattr(profile, "role", None)
        if role:
            token["role"] = role
        token["is_staff"] = bool(user.is_staff)
        token["is_superuser"] = bool(user.is_superuser)
        return token

    @property
    def access_token(self):
        access = super().access_token
        for claim in ("role", "is_staff", "is_superuser"):
            if claim in self:
                access[claim] = self[claim]
        return access
