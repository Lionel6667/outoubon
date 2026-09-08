from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0039_sitespotlight'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitespotlight',
            name='school',
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name='sitespotlight',
            name='score',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='sitespotlight',
            name='serie',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AlterField(
            model_name='sitespotlight',
            name='kind',
            field=models.CharField(
                choices=[
                    ('laureate', 'Lauréat du site'),
                    ('team_week', 'Équipe championne de la semaine'),
                    ('student_week', 'Meilleur élève de la semaine'),
                ],
                db_index=True,
                max_length=24,
            ),
        ),
    ]
