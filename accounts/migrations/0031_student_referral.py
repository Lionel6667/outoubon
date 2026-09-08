from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0030_visitor_tracking_precision'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='invite_code',
            field=models.CharField(blank=True, db_index=True, max_length=16, null=True, unique=True),
        ),
        migrations.CreateModel(
            name='StudentReferral',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reward_htg', models.PositiveIntegerField(default=250)),
                ('paid', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('referred_user', models.OneToOneField(
                    on_delete=models.CASCADE,
                    related_name='student_referral_record',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('referrer', models.ForeignKey(
                    on_delete=models.CASCADE,
                    related_name='student_referrals_made',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
