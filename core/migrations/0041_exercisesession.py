from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0040_sitespotlight_site_laureate'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExerciseSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(db_index=True, max_length=50)),
                ('chapter', models.CharField(blank=True, default='', max_length=200)),
                ('chapter_id', models.CharField(blank=True, default='', max_length=40)),
                ('title', models.CharField(blank=True, default='', max_length=220)),
                ('preview', models.CharField(blank=True, default='', max_length=280)),
                ('exercise_hash', models.CharField(db_index=True, max_length=32)),
                ('exercise', models.JSONField(default=dict)),
                ('messages', models.JSONField(default=list)),
                ('session_state', models.JSONField(default=dict)),
                ('is_favorite', models.BooleanField(db_index=True, default=False)),
                ('status', models.CharField(choices=[('active', 'En cours'), ('completed', 'Terminé')], default='active', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='exercise_sessions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='exercisesession',
            constraint=models.UniqueConstraint(fields=('user', 'exercise_hash'), name='uniq_user_exercise_hash'),
        ),
        migrations.AddIndex(
            model_name='exercisesession',
            index=models.Index(fields=['user', 'updated_at'], name='core_exerci_user_id_upd_idx'),
        ),
        migrations.AddIndex(
            model_name='exercisesession',
            index=models.Index(fields=['user', 'is_favorite', 'updated_at'], name='core_exerci_user_id_fav_idx'),
        ),
        migrations.AddIndex(
            model_name='exercisesession',
            index=models.Index(fields=['user', 'subject', 'updated_at'], name='core_exerci_user_id_sub_idx'),
        ),
    ]
