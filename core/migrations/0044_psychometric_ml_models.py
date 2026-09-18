# Generated for OU TOU BON ML Engine
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0043_userseenexamitem_succeeded'),
    ]

    operations = [
        migrations.CreateModel(
            name='TopicNode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('topic_id', models.SlugField(max_length=120, unique=True)),
                ('label', models.CharField(max_length=200)),
                ('subject', models.CharField(db_index=True, max_length=50)),
                ('serie', models.CharField(blank=True, default='', max_length=20)),
                ('chapter_ref', models.CharField(blank=True, default='', max_length=120)),
                ('prerequisites', models.ManyToManyField(blank=True, related_name='unlocks', to='core.topicnode')),
            ],
        ),
        migrations.CreateModel(
            name='ItemParameters',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item_uid', models.CharField(db_index=True, max_length=120, unique=True)),
                ('source_type', models.CharField(choices=[('quiz', 'Quiz'), ('exercice', 'Exercice BAC'), ('examen_blanc', 'Examen Blanc')], default='quiz', max_length=30)),
                ('difficulty_b', models.FloatField(default=0.0)),
                ('discrimination_a', models.FloatField(default=1.0)),
                ('n_responses', models.IntegerField(default=0)),
                ('last_calibrated', models.DateTimeField(auto_now=True)),
                ('topic', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='items', to='core.topicnode')),
            ],
        ),
        migrations.CreateModel(
            name='StudentAbility',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(db_index=True, max_length=50)),
                ('theta', models.FloatField(default=0.0)),
                ('theta_se', models.FloatField(default=1.0)),
                ('n_responses', models.IntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ml_abilities', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='TopicMastery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('p_mastery', models.FloatField(default=0.15)),
                ('n_observations', models.IntegerField(default=0)),
                ('last_updated', models.DateTimeField(auto_now=True)),
                ('topic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='student_masteries', to='core.topicnode')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ml_topic_masteries', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='MemoryCard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item_uid', models.CharField(blank=True, default='', max_length=120)),
                ('stability', models.FloatField(default=1.0)),
                ('difficulty', models.FloatField(default=5.0)),
                ('retrievability', models.FloatField(default=1.0)),
                ('reps', models.IntegerField(default=0)),
                ('lapses', models.IntegerField(default=0)),
                ('last_review', models.DateTimeField(blank=True, null=True)),
                ('next_review', models.DateTimeField(db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('topic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memory_cards', to='core.topicnode')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memory_cards', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PeerCluster',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cluster_id', models.IntegerField()),
                ('serie', models.CharField(db_index=True, max_length=20)),
                ('centroid_vector', models.JSONField(default=dict)),
                ('label', models.CharField(blank=True, default='', max_length=200)),
                ('computed_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'unique_together': {('cluster_id', 'serie')},
            },
        ),
        migrations.CreateModel(
            name='StudentClusterAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('distance_to_centroid', models.FloatField(default=0.0)),
                ('assigned_at', models.DateTimeField(auto_now=True)),
                ('cluster', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.peercluster')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='cluster_assignment', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='BacRiskPrediction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('p_reach_target', models.FloatField(default=0.5)),
                ('top_factors', models.JSONField(default=list)),
                ('computed_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='risk_prediction', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='BanditArmStats',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('arm_name', models.CharField(max_length=50)),
                ('alpha', models.FloatField(default=1.0)),
                ('beta', models.FloatField(default=1.0)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bandit_arms', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user', 'arm_name')},
            },
        ),
        migrations.AddIndex(
            model_name='topicnode',
            index=models.Index(fields=['subject', 'serie'], name='core_topicn_subject_e3a1f1_idx'),
        ),
        migrations.AddIndex(
            model_name='itemparameters',
            index=models.Index(fields=['topic'], name='core_itempa_topic_i_7bc1a2_idx'),
        ),
        migrations.AddIndex(
            model_name='itemparameters',
            index=models.Index(fields=['source_type'], name='core_itempa_source__e2b3c4_idx'),
        ),
        migrations.AddIndex(
            model_name='studentability',
            index=models.Index(fields=['user', 'subject'], name='core_studen_user_id_4d3e2a_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='studentability',
            unique_together={('user', 'subject')},
        ),
        migrations.AddIndex(
            model_name='topicmastery',
            index=models.Index(fields=['user', 'p_mastery'], name='core_topicm_user_id_9f8e7d_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='topicmastery',
            unique_together={('user', 'topic')},
        ),
        migrations.AddIndex(
            model_name='memorycard',
            index=models.Index(fields=['user', 'next_review'], name='core_memory_user_id_6a5b4c_idx'),
        ),
    ]
