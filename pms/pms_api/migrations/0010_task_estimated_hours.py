import decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pms_api", "0009_milestone_description"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="estimated_hours",
            field=models.DecimalField(
                decimal_places=2,
                default=decimal.Decimal("0"),
                help_text=(
                    "Planned effort for weighted progress; COMPLETED tasks count "
                    "full weight toward progress."
                ),
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(decimal.Decimal("0"))],
            ),
        ),
    ]
