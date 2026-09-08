import cloudinary.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0038_xp_ledger'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteSpotlight',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('laureate', 'Lauréat examens d’État'), ('team_week', 'Équipe championne de la semaine'), ('student_week', 'Meilleur élève de la semaine')], db_index=True, max_length=24)),
                ('title', models.CharField(max_length=140)),
                ('subtitle', models.CharField(blank=True, max_length=180)),
                ('body', models.TextField(blank=True)),
                ('academic_year', models.CharField(blank=True, default='2025-2026', max_length=16)),
                ('week_label', models.CharField(blank=True, max_length=80)),
                ('photo', cloudinary.models.CloudinaryField(blank=True, max_length=255, null=True, verbose_name='spotlight')),
                ('is_published', models.BooleanField(db_index=True, default=True)),
                ('pin_order', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['pin_order', '-created_at'],
            },
        ),
    ]
