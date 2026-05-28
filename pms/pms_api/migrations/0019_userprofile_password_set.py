from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0018_task_self_created_supervisor_nullable_project"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="password_set",
            field=models.BooleanField(default=False),
        ),
    ]
