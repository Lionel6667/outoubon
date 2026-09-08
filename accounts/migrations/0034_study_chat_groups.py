from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_official_group(apps, schema_editor):
    StudyChatGroup = apps.get_model('accounts', 'StudyChatGroup')
    StudyChatGroup.objects.get_or_create(
        slug='otb-official',
        defaults={'name': 'Groupe OU TOU BON', 'kind': 'official'},
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0033_xp_withdrawal'),
    ]

    operations = [
        migrations.CreateModel(
            name='StudyChatGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('slug', models.SlugField(max_length=80, unique=True)),
                ('kind', models.CharField(choices=[('official', 'OU TOU BON'), ('genius', 'Génies'), ('custom', 'Groupe')], db_index=True, max_length=16)),
                ('genius_team_id', models.IntegerField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_study_groups', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['kind', 'created_at'],
            },
        ),
        migrations.CreateModel(
            name='StudyChatGroupMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(default='member', max_length=16)),
                ('last_read_id', models.PositiveIntegerField(default=0)),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='accounts.studychatgroup')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='study_group_memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('group', 'user')},
            },
        ),
        migrations.AddIndex(
            model_name='studychatgroupmember',
            index=models.Index(fields=['user', 'group'], name='accounts_st_user_id_6f4e1a_idx'),
        ),
        migrations.AddField(
            model_name='groupmessage',
            name='group',
            field=models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='accounts.studychatgroup'),
        ),
        migrations.AddIndex(
            model_name='groupmessage',
            index=models.Index(fields=['group', 'id'], name='accounts_gr_group_i_9c2e4b_idx'),
        ),
        migrations.RunPython(create_official_group, migrations.RunPython.noop),
    ]
