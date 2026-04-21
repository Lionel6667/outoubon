from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_courseprogressstate'),
    ]

    operations = [
        migrations.CreateModel(
            name='GeneratedCourseAsset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('course_key', models.CharField(db_index=True, max_length=100)),
                ('section_id', models.CharField(db_index=True, max_length=120)),
                ('section_title', models.CharField(blank=True, default='', max_length=255)),
                ('asset_type', models.CharField(choices=[('lesson', 'Lecon'), ('quiz', 'Mini quiz')], max_length=20)),
                ('mode', models.CharField(default='normal', max_length=20)),
                ('payload', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['course_key', 'section_id', 'asset_type'],
                'unique_together': {('course_key', 'section_id', 'asset_type', 'mode')},
            },
        ),
    ]