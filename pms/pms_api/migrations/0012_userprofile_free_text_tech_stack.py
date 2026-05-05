from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0011_notification_details"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="tech_stack",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
