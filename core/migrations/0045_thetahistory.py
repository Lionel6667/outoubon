# Generated for ThetaHistory daily snapshots
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0044_psychometric_ml_models'),
    ]

    operations = [
        migrations.CreateModel(
            name='ThetaHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(max_length=30)),
                ('date', models.DateField(db_index=True)),
                ('theta', models.FloatField()),
                ('theta_se', models.FloatField()),
                ('display_score', models.FloatField()),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='theta_history', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user', 'subject', 'date')},
            },
        ),
        migrations.AddIndex(
            model_name='thetahistory',
            index=models.Index(fields=['user', 'subject', 'date'], name='core_thetah_user_id_0bc91a_idx'),
        ),
    ]
