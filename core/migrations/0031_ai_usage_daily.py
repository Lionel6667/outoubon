# Generated manually for AiUsageDaily micro-log

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0030_user_seen_exam_item'),
    ]

    operations = [
        migrations.CreateModel(
            name='AiUsageDaily',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('guest_key', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('date', models.DateField(db_index=True)),
                ('feature', models.CharField(
                    choices=[
                        ('chat', 'Chat tuteur'),
                        ('exercise', 'Exercices'),
                        ('exam', 'Examen blanc'),
                        ('fiches', 'Fiches mémo'),
                        ('course', 'Cours interactif'),
                        ('quiz', 'Quiz'),
                        ('other', 'Autre'),
                    ],
                    db_index=True,
                    default='other',
                    max_length=32,
                )),
                ('api_calls', models.PositiveIntegerField(default=0)),
                ('prompt_tokens', models.PositiveIntegerField(default=0)),
                ('completion_tokens', models.PositiveIntegerField(default=0)),
                ('cache_hit_tokens', models.PositiveIntegerField(default=0)),
                ('cost_usd_micro', models.PositiveIntegerField(
                    default=0,
                    help_text='Coût estimé en millionièmes de USD (1 = 0.000001 $)',
                )),
                ('user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='ai_usage_daily',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-date', '-prompt_tokens'],
            },
        ),
        migrations.AddIndex(
            model_name='aiusagedaily',
            index=models.Index(fields=['date', 'feature'], name='core_aiusag_date_fe_idx'),
        ),
        migrations.AddIndex(
            model_name='aiusagedaily',
            index=models.Index(fields=['user', 'date'], name='core_aiusag_user_id_idx'),
        ),
        migrations.AddConstraint(
            model_name='aiusagedaily',
            constraint=models.UniqueConstraint(
                fields=('user', 'guest_key', 'date', 'feature'),
                name='unique_ai_usage_daily_actor',
            ),
        ),
    ]
