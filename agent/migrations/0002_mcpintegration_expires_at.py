from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("agent", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpintegration",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
