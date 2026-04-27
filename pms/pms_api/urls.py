from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdminAIAskAPIView,
    AdminForgotPasswordRequestOTPAPIView,
    AdminForgotPasswordVerifyOTPAPIView,
    AdminPasswordResetAPIView,
    AdminOverviewAPIView,
    DashboardAPIView,
    FileAttachmentViewSet,
    LoginAPIView,
    MeAPIView,
    MilestoneViewSet,
    MyTasksAPIView,
    NotificationViewSet,
    ProjectViewSet,
    RefreshAPIView,
    TaskViewSet,
    WorkTrackingAPIView,
    UserViewSet,
)

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="users")
router.register(r"projects", ProjectViewSet, basename="projects")
router.register(r"milestones", MilestoneViewSet, basename="milestones")
router.register(r"tasks", TaskViewSet, basename="tasks")
router.register(r"files", FileAttachmentViewSet, basename="files")
router.register(r"notifications", NotificationViewSet, basename="notifications")

urlpatterns = [
    path("auth/login", LoginAPIView.as_view(), name="auth-login"),
    path("auth/admin/forgot-password/request-otp", AdminForgotPasswordRequestOTPAPIView.as_view(), name="admin-forgot-password-request-otp"),
    path("auth/admin/forgot-password/verify-otp", AdminForgotPasswordVerifyOTPAPIView.as_view(), name="admin-forgot-password-verify-otp"),
    path("auth/refresh", RefreshAPIView.as_view(), name="auth-refresh"),
    path("auth/me", MeAPIView.as_view(), name="auth-me"),
    path("my/tasks", MyTasksAPIView.as_view(), name="my-tasks"),
    path("admin/dashboard", DashboardAPIView.as_view(), name="admin-dashboard"),
    path("admin/overview", AdminOverviewAPIView.as_view(), name="admin-overview"),
    path("admin/ai/ask", AdminAIAskAPIView.as_view(), name="admin-ai-ask"),
    path("work-tracking", WorkTrackingAPIView.as_view(), name="work-tracking"),
    path("admin/reset-password", AdminPasswordResetAPIView.as_view(), name="admin-reset-password"),
    path("ba/dashboard", DashboardAPIView.as_view(), name="ba-dashboard"),
    path("employee/dashboard", DashboardAPIView.as_view(), name="employee-dashboard"),
    path("", include(router.urls)),
]
