from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0002_milestone_milestone_no"),
    ]

    operations = [
        migrations.AddField(
            model_name="fileattachment",
            name="milestone",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="files", to="pms_api.milestone"),
        ),
        migrations.AddField(
            model_name="fileattachment",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="files", to="pms_api.project"),
        ),
    ]

