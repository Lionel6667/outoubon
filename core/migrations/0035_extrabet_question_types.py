from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0034_postcomment_postcommentattachment_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='extrabetpost',
            name='question_type',
            field=models.CharField(
                choices=[
                    ('word', 'Un mot'),
                    ('qcm', 'QCM'),
                    ('match', 'Relier (flèches)'),
                    ('parts', 'Exercice a) b) c)'),
                    ('direct', 'Réponse directe (ancien)'),
                    ('fill', 'Texte à compléter (ancien)'),
                ],
                default='word',
                max_length=12,
            ),
        ),
    ]
