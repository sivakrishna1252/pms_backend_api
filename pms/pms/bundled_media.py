"""Serve bundled repo files under fixed /media/... paths (works even if MEDIA_ROOT is empty)."""

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404


def api_master_guide_md(_request):
    path = Path(settings.BASE_DIR) / "API_MASTER_GUIDE.md"
    if not path.is_file():
        raise Http404("API_MASTER_GUIDE.md is not installed in the app image.")
    return FileResponse(
        path.open("rb"),
        content_type="text/markdown; charset=utf-8",
    )
