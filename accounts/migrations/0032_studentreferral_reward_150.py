from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0031_student_referral'),
    ]

    operations = [
        migrations.AlterField(
            model_name='studentreferral',
            name='reward_htg',
            field=models.PositiveIntegerField(default=150),
        ),
    ]
