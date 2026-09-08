from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('genius', '0003_join_requests_and_captain_eligible'),
    ]

    operations = [
        migrations.AddField(
            model_name='geniusteamstats',
            name='team_streak',
            field=models.PositiveIntegerField(default=0, help_text='Victoires consécutives.'),
        ),
        migrations.AddField(
            model_name='geniusteamstats',
            name='team_xp',
            field=models.PositiveIntegerField(default=0, help_text='XP équipe (lien progression OU TOU BON).'),
        ),
    ]
