from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0008_userprofile_tech_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="milestone",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
    ]

