from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_add_preferred_lang'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='langue_etrangere',
            field=models.CharField(
                choices=[('anglais', 'Anglais'), ('espagnol', 'Espagnol')],
                default='anglais',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='diagnosticresult',
            name='subject',
            field=models.CharField(
                choices=[
                    ('maths', 'Maths'),
                    ('physique', 'Physique'),
                    ('chimie', 'Chimie'),
                    ('svt', 'SVT'),
                    ('francais', 'Français'),
                    ('philosophie', 'Philosophie'),
                    ('histoire', 'Histoire & Géo'),
                    ('anglais', 'Anglais'),
                    ('espagnol', 'Espagnol'),
                ],
                max_length=50,
            ),
        ),
    ]
