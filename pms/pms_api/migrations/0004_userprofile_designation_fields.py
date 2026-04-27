from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0003_fileattachment_milestone_fileattachment_project"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="department",
            field=models.CharField(
                blank=True,
                choices=[("BACKEND", "Backend"), ("FRONTEND", "Frontend"), ("FULLSTACK", "Fullstack")],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="seniority",
            field=models.CharField(
                blank=True, choices=[("JUNIOR", "Junior"), ("SENIOR", "Senior")], max_length=20
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="tech_stack",
            field=models.CharField(
                blank=True,
                choices=[
                    ("PYTHON", "Python"),
                    ("JAVA", "Java"),
                    ("NESTJS", "NestJS"),
                    ("NEXTJS", "NextJS"),
                    ("REACT", "React"),
                ],
                max_length=20,
            ),
        ),
    ]
