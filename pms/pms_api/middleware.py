"""Run Mon-Sat 8 PM task auto-stop once per day when API traffic hits the backend."""

class EveningTaskAutoStopMiddleware:
    """
    Backup when host cron misses: run the evening pass at most once per Mon-Sat date.
    Does not stop timers started after that evening pass until the next 8 PM run.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/"):
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                self._maybe_run_evening_auto_stop()
        return self.get_response(request)

    def _maybe_run_evening_auto_stop(self) -> None:
        from pms_api.timer_auto_stop import run_evening_auto_stop_if_due
        from pms_api.views import _sync_parent_statuses_for_task

        run_evening_auto_stop_if_due(
            force=False,
            notify=True,
            on_task_sync=_sync_parent_statuses_for_task,
        )
