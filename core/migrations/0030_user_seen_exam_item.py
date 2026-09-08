# Generated manually for Sprint 1 — ExamItemRegistry history

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_revisionplan_completed_tasks'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserSeenExamItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(db_index=True, max_length=50)),
                ('item_hash', models.CharField(db_index=True, max_length=64)),
                ('seen_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='seen_exam_items', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-seen_at'],
            },
        ),
        migrations.AddIndex(
            model_name='userseenexamitem',
            index=models.Index(fields=['user', 'subject'], name='core_userse_user_id_8a3f21_idx'),
        ),
        migrations.AddConstraint(
            model_name='userseenexamitem',
            constraint=models.UniqueConstraint(fields=('user', 'subject', 'item_hash'), name='unique_user_subject_exam_item'),
        ),
    ]
