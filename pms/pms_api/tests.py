from datetime import datetime
from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone

from .models import Task
from .progress import planned_hours_for_task


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
