from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0027_group_reply_mentions_notifications'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyusage',
            name='ai_request_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
