from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0012_userprofile_free_text_tech_stack"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="department",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
