# Generated manually for evening task auto-stop run tracking.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0024_rename_pms_api_pro_project_8a1f0d_idx_pms_api_pro_project_1a81d5_idx"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskEveningAutoStopRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("run_date", models.DateField(unique=True)),
            ],
            options={
                "ordering": ["-run_date"],
            },
        ),
    ]
