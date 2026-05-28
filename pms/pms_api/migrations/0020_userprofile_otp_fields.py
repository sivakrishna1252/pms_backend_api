from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0019_userprofile_password_set"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="first_login_otp",
            field=models.CharField(blank=True, default="", max_length=6),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="first_login_otp_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="password_reset_otp",
            field=models.CharField(blank=True, default="", max_length=6),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="password_reset_otp_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
