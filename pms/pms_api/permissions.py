from rest_framework.permissions import BasePermission

from .models import UserProfile
from .service_auth import is_valid_service_authorization


def user_role(user):
    profile = getattr(user, "profile", None)
    return getattr(profile, "role", None)


def effective_portal_role(user):
    """
    Portal role for login, /auth/me, and route guards.
    UserProfile.role is authoritative for ADMIN / BA / EMPLOYEE.
    Django superusers without a profile role fall back to ADMIN (bootstrap accounts).
    is_staff alone does NOT imply ADMIN — it is only for Django admin access.
    """
    role = user_role(user)
    if role in {
        UserProfile.Roles.ADMIN,
        UserProfile.Roles.BA,
        UserProfile.Roles.EMPLOYEE,
    }:
        return role
    if getattr(user, "is_superuser", False):
        return UserProfile.Roles.ADMIN
    return None




class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and user_role(request.user) == UserProfile.Roles.ADMIN
        )




class IsAdminOrBA(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and user_role(request.user) in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}
        )




class IsEmployee(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and user_role(request.user) == UserProfile.Roles.EMPLOYEE
        )


class IsServiceToken(BasePermission):
    """Attendance service: PMS_SERVICE_TOKEN or derived token from shared Django secret."""

    def has_permission(self, request, view):
        return is_valid_service_authorization(request.headers.get("Authorization"))
