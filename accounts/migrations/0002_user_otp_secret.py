# Generated migration for User.otp_secret

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='otp_secret',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
