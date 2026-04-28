from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0007_milestone_document_project_document_task_document"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="tech_notes",
            field=models.TextField(blank=True, default=""),
        ),
    ]
