from django.db import migrations, models
from django.db.models import F


def backfill_last_activity(apps, schema_editor):
    TimeLog = apps.get_model("pms_api", "TimeLog")
    TimeLog.objects.filter(last_activity_at__isnull=True).update(last_activity_at=F("start_time"))


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0013_userprofile_free_text_department"),
    ]

    operations = [
        migrations.AddField(
            model_name="timelog",
            name="last_activity_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_last_activity, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="timelog",
            name="last_activity_at",
            field=models.DateTimeField(),
        ),
    ]
