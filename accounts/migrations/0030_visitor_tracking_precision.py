# Generated manually for visitor / activity precision
from django.db import migrations, models


def backfill_visit_dates(apps, schema_editor):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE accounts_sitevisit SET visit_date = DATE(visited_at) WHERE visit_date IS NULL"
        )


def dedupe_visits(apps, schema_editor):
    """Ancien tracking : plusieurs lignes / jour pour le même ip_hash."""
    from django.db import connection
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(
                """
                DELETE FROM accounts_sitevisit a
                USING accounts_sitevisit b
                WHERE a.id > b.id
                  AND a.ip_hash = b.ip_hash
                  AND a.visit_date IS NOT NULL
                  AND a.visit_date = b.visit_date
                """
            )
        else:
            # SQLite (et autres) : pas de DELETE ... USING.
            cursor.execute(
                """
                DELETE FROM accounts_sitevisit
                WHERE id NOT IN (
                    SELECT MIN(id) FROM accounts_sitevisit
                    WHERE visit_date IS NOT NULL
                    GROUP BY ip_hash, visit_date
                )
                AND visit_date IS NOT NULL
                """
            )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0029_userprofile_coach_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitevisit',
            name='country_code',
            field=models.CharField(blank=True, db_index=True, default='', max_length=2),
        ),
        migrations.AddField(
            model_name='sitevisit',
            name='visit_date',
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='last_seen_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_visit_dates, migrations.RunPython.noop),
        migrations.RunPython(dedupe_visits, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='sitevisit',
            constraint=models.UniqueConstraint(
                fields=('ip_hash', 'visit_date'),
                name='unique_sitevisit_ip_per_day',
            ),
        ),
    ]
