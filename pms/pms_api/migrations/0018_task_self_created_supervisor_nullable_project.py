from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("pms_api", "0017_bigautofield_primary_keys"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="is_self_created",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="task",
            name="supervisor",
            field=models.ForeignKey(
                blank=True,
                help_text="Admin/BA chosen by employee for self-created tasks.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="supervised_self_tasks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="task",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tasks",
                to="pms_api.project",
            ),
        ),
    ]
