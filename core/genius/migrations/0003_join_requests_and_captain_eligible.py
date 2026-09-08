# Generated manually for genius join requests

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('genius', '0002_geniusteamstats_geniusnotification'),
    ]

    operations = [
        migrations.AddField(
            model_name='geniusteam',
            name='is_open_for_requests',
            field=models.BooleanField(
                default=True,
                help_text='Équipe visible : les joueurs peuvent demander à rejoindre.',
            ),
        ),
        migrations.AddField(
            model_name='geniusmembership',
            name='captain_eligible',
            field=models.BooleanField(
                default=True,
                help_text="False si l'ancien capitaine a transféré son rôle — ne peut plus être capitaine.",
            ),
        ),
        migrations.CreateModel(
            name='GeniusJoinRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.CharField(blank=True, default='', max_length=200)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'En attente'),
                        ('accepted', 'Acceptée'),
                        ('rejected', 'Refusée'),
                        ('cancelled', 'Annulée'),
                    ],
                    default='pending',
                    max_length=12,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('from_user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='genius_join_requests_sent',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('team', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='join_requests',
                    to='genius.geniusteam',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='geniusjoinrequest',
            index=models.Index(fields=['team', 'status'], name='genius_geni_team_id_8a0f2a_idx'),
        ),
        migrations.AddIndex(
            model_name='geniusjoinrequest',
            index=models.Index(fields=['from_user', 'status'], name='genius_geni_from_us_6c8b1d_idx'),
        ),
        migrations.AlterField(
            model_name='geniusnotification',
            name='notification_type',
            field=models.CharField(db_index=True, max_length=24, choices=[
                ('invite', 'Invitation équipe'),
                ('challenge', 'Défi reçu'),
                ('match_ready', 'Match à jouer'),
                ('match_result', 'Résultat match'),
                ('competition_start', 'Concours lancé'),
                ('bracket_ready', 'Bracket publié'),
                ('join_request', 'Demande de rejoindre'),
                ('join_request_accepted', 'Demande acceptée'),
                ('captain_transfer', 'Transfert capitaine'),
            ]),
        ),
    ]
