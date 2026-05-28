from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0020_userprofile_otp_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="first_login_token_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="first_login_token_hash",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
