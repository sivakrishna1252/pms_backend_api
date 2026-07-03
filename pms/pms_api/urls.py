from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .export_views import (
    MilestonesExportAPIView,
    ProjectsExportAPIView,
    TasksExportAPIView,
)
from .views import (
    AdminAIAskAPIView,
    AdminAIHealthAPIView,
    AdminForgotPasswordRequestOTPAPIView,
    AdminForgotPasswordVerifyOTPAPIView,
    AdminPasswordResetAPIView,
    AdminProjectOverviewAPIView,
    FirstLoginRequestOTPAPIView,
    FirstLoginResendLinkAPIView,
    FirstLoginSetPasswordAPIView,
    FirstLoginVerifyOTPAPIView,
    AdminOverviewAPIView,
    DashboardAPIView,
    FileAttachmentViewSet,
    LoginAPIView,
    MeAPIView,
    MilestoneViewSet,
    MyTasksAPIView,
    InternalAdminUsersAPIView,
    InternalNotificationCreateAPIView,
    InternalStaffUsersAPIView,
    InternalUserDetailAPIView,
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

project_detail_view = ProjectViewSet.as_view({"get": "project_detail"})

urlpatterns = [
    path("auth/login", LoginAPIView.as_view(), name="auth-login"),
    path("auth/first-login/request-otp", FirstLoginRequestOTPAPIView.as_view(), name="first-login-request-otp"),
    path("auth/first-login/verify-otp", FirstLoginVerifyOTPAPIView.as_view(), name="first-login-verify-otp"),
    path("auth/first-login/set-password", FirstLoginSetPasswordAPIView.as_view(), name="first-login-set-password"),
    path("auth/first-login/resend-link", FirstLoginResendLinkAPIView.as_view(), name="first-login-resend-link"),
    path("auth/forgot-password/request-otp", AdminForgotPasswordRequestOTPAPIView.as_view(), name="forgot-password-request-otp"),
    path("auth/forgot-password/verify-otp", AdminForgotPasswordVerifyOTPAPIView.as_view(), name="forgot-password-verify-otp"),
    path("auth/admin/forgot-password/request-otp", AdminForgotPasswordRequestOTPAPIView.as_view(), name="admin-forgot-password-request-otp"),
    path("auth/admin/forgot-password/verify-otp", AdminForgotPasswordVerifyOTPAPIView.as_view(), name="admin-forgot-password-verify-otp"),
    path("auth/refresh", RefreshAPIView.as_view(), name="auth-refresh"),
    path("auth/me", MeAPIView.as_view(), name="auth-me"),
    path("my/tasks", MyTasksAPIView.as_view(), name="my-tasks"),
    path("admin/dashboard", DashboardAPIView.as_view(), name="admin-dashboard"),
    path(
        "admin/dashboard/project-overview",
        AdminProjectOverviewAPIView.as_view(),
        name="admin-dashboard-project-overview",
    ),
    path("admin/overview", AdminOverviewAPIView.as_view(), name="admin-overview"),
    path("admin/exports/projects/", ProjectsExportAPIView.as_view(), name="admin-export-projects"),
    path("admin/exports/milestones/", MilestonesExportAPIView.as_view(), name="admin-export-milestones"),
    path("admin/exports/tasks/", TasksExportAPIView.as_view(), name="admin-export-tasks"),
    path("admin/ai/health", AdminAIHealthAPIView.as_view(), name="admin-ai-health"),
    path("admin/ai/ask", AdminAIAskAPIView.as_view(), name="admin-ai-ask"),
    path("work-tracking", WorkTrackingAPIView.as_view(), name="work-tracking"),
    path("admin/reset-password", AdminPasswordResetAPIView.as_view(), name="admin-reset-password"),
    path("ba/dashboard", DashboardAPIView.as_view(), name="ba-dashboard"),
    path("employee/dashboard", DashboardAPIView.as_view(), name="employee-dashboard"),
    path(
        "internal/admin-users/",
        InternalAdminUsersAPIView.as_view(),
        name="internal-admin-users",
    ),
    path(
        "internal/staff-users/",
        InternalStaffUsersAPIView.as_view(),
        name="internal-staff-users",
    ),
    path(
        "internal/users/<int:user_id>/",
        InternalUserDetailAPIView.as_view(),
        name="internal-user-detail",
    ),
    path(
        "internal/notifications/",
        InternalNotificationCreateAPIView.as_view(),
        name="internal-notifications",
    ),
    # Explicit route so production/nginx always resolves project detail (DRF router also registers this).
    path("projects/<int:pk>/detail/", project_detail_view, name="project-detail"),
    path("", include(router.urls)),
]
