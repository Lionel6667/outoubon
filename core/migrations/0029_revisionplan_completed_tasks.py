from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_dailyusage_ai_request_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='revisionplan',
            name='completed_tasks',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
