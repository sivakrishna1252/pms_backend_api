from django.db import migrations, models


def populate_milestone_numbers(apps, schema_editor):
    Milestone = apps.get_model("pms_api", "Milestone")

    project_ids = Milestone.objects.values_list("project_id", flat=True).distinct()
    for project_id in project_ids:
        milestones = Milestone.objects.filter(project_id=project_id).order_by("created_at", "id")
        for index, milestone in enumerate(milestones, start=1):
            milestone.milestone_no = index
            milestone.save(update_fields=["milestone_no"])


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="milestone",
            name="milestone_no",
            field=models.PositiveIntegerField(default=1),
            preserve_default=False,
        ),
        migrations.RunPython(populate_milestone_numbers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="milestone",
            constraint=models.UniqueConstraint(fields=("project", "milestone_no"), name="uniq_milestone_no_per_project"),
        ),
    ]
