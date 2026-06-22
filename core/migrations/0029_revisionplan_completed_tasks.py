from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_generated_exam_cache'),
    ]

    operations = [
        migrations.AddField(
            model_name='revisionplan',
            name='completed_tasks',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
