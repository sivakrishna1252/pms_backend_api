"""
URL configuration for pms project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path, re_path
from django.http import JsonResponse
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
import pms_api.schema  # Register OpenAPI auth extension for docs generation.
from pms.bundled_media import api_master_guide_md


def home(request):
    return JsonResponse({"message":"Welcome to Project Management System backend"})

urlpatterns = [
    # Bundled doc: always served from repo root ( survives empty MEDIA_ROOT / failed copy ).
    path("media/project_docs/API_MASTER_GUIDE.md", api_master_guide_md),
    path('admin/', admin.site.urls),
    path('',home),
    path("api/v1/", include("pms_api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("api/docs/swagger/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="swagger-ui"),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="api-schema"), name="redoc-ui"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # django.conf.urls.static.static() only registers when DEBUG — still serve user uploads in prod.
    media_prefix = settings.MEDIA_URL.lstrip("/").rstrip("/")
    if media_prefix:
        urlpatterns += [
            re_path(
                rf"^{media_prefix}/(?P<path>.*)$",
                serve,
                {"document_root": str(settings.MEDIA_ROOT.resolve())},
            ),
        ]
