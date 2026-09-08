from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0036_match_arena'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='quizduel',
            name='current_q_index',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='is_live_race',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='is_mixed_subjects',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='live_phase',
            field=models.CharField(blank=True, default='', max_length=12),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='question_deadline',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='question_winner',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='duel_question_wins',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='reveal_deadline',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
