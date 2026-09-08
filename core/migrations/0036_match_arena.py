# Generated manually for Match Arena

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0035_extrabet_question_types'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='quizduel',
            name='ghost_answer_plan',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='ghost_avatar_emoji',
            field=models.CharField(blank=True, default='🎓', max_length=8),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='ghost_display_name',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='is_ghost_opponent',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='quizduel',
            name='match_mode',
            field=models.CharField(default='private', max_length=12),
        ),
        migrations.CreateModel(
            name='MatchQueueEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(db_index=True, max_length=50)),
                ('status', models.CharField(db_index=True, default='waiting', max_length=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('duel', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='queue_entries', to='core.quizduel')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='match_queue_entries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='matchqueueentry',
            index=models.Index(fields=['status', 'subject', 'created_at'], name='core_matchq_status_subj_idx'),
        ),
    ]
