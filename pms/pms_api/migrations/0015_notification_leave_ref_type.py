from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0014_timelog_last_activity_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="ref_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("TASK", "Task"),
                    ("MILESTONE", "Milestone"),
                    ("PROJECT", "Project"),
                    ("LEAVE", "Leave"),
                ],
                max_length=20,
            ),
        ),
    ]
