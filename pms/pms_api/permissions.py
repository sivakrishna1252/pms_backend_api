from rest_framework.permissions import BasePermission
from .models import UserProfile


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
