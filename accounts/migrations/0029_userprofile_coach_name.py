from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0028_dailyusage_ai_request_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='coach_name',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Nom personnel choisi pour l'assistant IA",
                max_length=40,
            ),
        ),
    ]
