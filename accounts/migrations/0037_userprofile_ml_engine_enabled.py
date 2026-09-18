# Generated for OU TOU BON ML Engine
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0036_push_device'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='ml_engine_enabled',
            field=models.BooleanField(default=True, help_text='Active les moteurs psychométriques IRT, BKT et FSRS'),
        ),
    ]
