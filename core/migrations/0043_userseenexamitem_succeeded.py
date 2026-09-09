from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0042_sitespotlight_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='userseenexamitem',
            name='succeeded',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
