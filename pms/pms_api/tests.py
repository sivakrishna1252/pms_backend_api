from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from .models import Task
from .progress import planned_hours_for_task
from .timer_logs_visibility import timer_logs_visible_from_date


class ProgressCalculationTests(SimpleTestCase):
    def test_planned_hours_fallback_skips_weekends(self):
        task = Task(
            title="Weekend span task",
            estimated_hours=Decimal("0"),
            deadline=datetime(2026, 5, 11).date(),
        )
        task.created_at = timezone.make_aware(datetime(2026, 5, 6, 9, 0, 0))

        self.assertEqual(planned_hours_for_task(task), Decimal("32"))

    def test_estimated_hours_override_still_used(self):
        task = Task(
            title="Estimated task",
            estimated_hours=Decimal("6.5"),
            deadline=datetime(2026, 5, 11).date(),
        )
        task.created_at = timezone.make_aware(datetime(2026, 5, 6, 9, 0, 0))

        self.assertEqual(planned_hours_for_task(task), Decimal("6.5"))


class TimerLogsVisibilityTests(SimpleTestCase):
    @override_settings(TIMER_LOGS_VISIBLE_FROM=datetime(2026, 7, 2).date())
    def test_configured_visible_from_date(self):
        self.assertEqual(timer_logs_visible_from_date(), datetime(2026, 7, 2).date())

    @override_settings(TIMER_LOGS_VISIBLE_FROM=None)
    @patch("pms_api.timer_logs_visibility.timezone.localdate", return_value=datetime(2026, 7, 2).date())
    def test_default_visible_from_is_local_today(self, _mock_localdate):
        self.assertEqual(timer_logs_visible_from_date(), datetime(2026, 7, 2).date())
