from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_subjectmastery_learningevent_chatsessionsummary'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExtraBetPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(db_index=True, max_length=50)),
                ('question_type', models.CharField(choices=[('direct', 'Réponse directe'), ('fill', 'Texte à compléter'), ('qcm', 'Choix multiple')], default='direct', max_length=10)),
                ('prompt', models.TextField()),
                ('answer', models.TextField()),
                ('options', models.JSONField(blank=True, default=list)),
                ('ai_verdict', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='extra_bet_posts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['subject', 'created_at'], name='core_extrabe_subject_c2dfda_idx')],
            },
        ),
        migrations.CreateModel(
            name='ExtraBetAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('submitted_answer', models.TextField()),
                ('is_correct', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attempts', to='core.extrabetpost')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='extra_bet_attempts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-updated_at'],
                'unique_together': {('post', 'user')},
            },
        ),
    ]
