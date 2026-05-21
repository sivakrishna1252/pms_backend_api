from rest_framework.permissions import BasePermission

from .models import UserProfile
from .service_auth import is_valid_service_authorization


def user_role(user):
    profile = getattr(user, "profile", None)
    return getattr(profile, "role", None)




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
