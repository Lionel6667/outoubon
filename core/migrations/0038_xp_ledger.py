import uuid

from django.conf import settings
from django.db import migrations, models


def seed_legacy_xp(apps, schema_editor):
    UserStats = apps.get_model('core', 'UserStats')
    XpEvent = apps.get_model('core', 'XpEvent')
    for stats in UserStats.objects.all().iterator():
        amount = (
            int(stats.quiz_completes or 0) * 20
            + int(stats.exercices_resolus or 0) * 50
            + int(stats.messages_envoyes or 0) * 5
        )
        if amount <= 0:
            if stats.xp_total:
                continue
            stats.xp_total = 0
            stats.save(update_fields=['xp_total'])
            continue
        _, created = XpEvent.objects.get_or_create(
            user_id=stats.user_id,
            source='LEGACY',
            reference=f'legacy:{stats.user_id}',
            defaults={'amount': amount, 'extra': {'migrated': True}},
        )
        if created or stats.xp_total < amount:
            stats.xp_total = max(int(stats.xp_total or 0), amount)
            stats.save(update_fields=['xp_total'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0037_live_race_duel'),
    ]

    operations = [
        migrations.AddField(
            model_name='userstats',
            name='xp_total',
            field=models.PositiveIntegerField(db_index=True, default=0),
        ),
        migrations.CreateModel(
            name='XpEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.IntegerField(default=0)),
                ('source', models.CharField(db_index=True, max_length=32)),
                ('reference', models.CharField(db_index=True, max_length=180)),
                ('extra', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('user', models.ForeignKey(on_delete=models.CASCADE, related_name='xp_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='XpActivity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField(db_index=True, default=uuid.uuid4, unique=True)),
                ('kind', models.CharField(db_index=True, max_length=16)),
                ('subject', models.CharField(blank=True, default='', max_length=50)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=models.CASCADE, related_name='xp_activities', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='xpevent',
            constraint=models.UniqueConstraint(fields=('user', 'source', 'reference'), name='uniq_xp_event_user_source_ref'),
        ),
        migrations.AddIndex(
            model_name='xpactivity',
            index=models.Index(fields=['user', 'kind', 'created_at'], name='core_xpacti_user_id_kind_idx'),
        ),
        migrations.RunPython(seed_legacy_xp, noop_reverse),
    ]
