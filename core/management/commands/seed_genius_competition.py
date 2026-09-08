"""
Seed a demo Groupe de Génies competition with optional demo teams.

Usage:
    python manage.py seed_genius_competition
    python manage.py seed_genius_competition --teams 4 --start
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.genius.constants import MAX_TEAM_SIZE
from core.genius.models import GeniusCompetition, GeniusRegistration, GeniusTeam
from core.genius.services import create_team, join_team_with_code, lock_roster, start_competition

User = get_user_model()


class Command(BaseCommand):
    help = 'Create a demo Genius competition and optional demo teams.'

    def add_arguments(self, parser):
        parser.add_argument('--teams', type=int, default=0, help='Create N demo teams (4 players each)')
        parser.add_argument('--start', action='store_true', help='Lock rosters and generate bracket')
        parser.add_argument('--name', default='Coupe des Génies — Démo')

    @transaction.atomic
    def handle(self, *args, **options):
        n_teams = max(0, options['teams'])
        comp = GeniusCompetition.objects.create(
            name=options['name'],
            description='Concours démo pour tester le bracket et les matchs synchronisés.',
            status='registration',
            start_date=date.today(),
            end_date=date.today() + timedelta(days=30),
            max_teams=32,
            prizes={
                '1': {'place': '🥇', 'label': 'Champions', 'reward': 'Badge + 1 mois Premium'},
                '2': {'place': '🥈', 'label': 'Finalistes', 'reward': 'Badge équipe'},
                '3': {'place': '🥉', 'label': 'Demi-finalistes', 'reward': 'Points bonus'},
            },
        )
        self.stdout.write(self.style.SUCCESS(f'Competition created: id={comp.id} "{comp.name}"'))

        team_ids = []
        for i in range(n_teams):
            cap_username = f'genius_cap_{comp.id}_{i}'
            captain, _ = User.objects.get_or_create(username=cap_username, defaults={'first_name': f'Cap{i}'})
            if not captain.has_usable_password():
                captain.set_password('demo-genius-pass')
                captain.save()

            existing = GeniusTeam.objects.filter(captain=captain, is_active=True).first()
            if existing:
                team = existing
            else:
                team = create_team(captain, f'Équipe Démo {i + 1}', emblem='🧠')
                for j in range(MAX_TEAM_SIZE - 1):
                    u, _ = User.objects.get_or_create(
                        username=f'genius_{comp.id}_{i}_{j}',
                        defaults={'first_name': f'P{j}'},
                    )
                    if not u.has_usable_password():
                        u.set_password('demo-genius-pass')
                        u.save()
                    if not team.memberships.filter(user=u, status='active').exists():
                        join_team_with_code(u, team.invite_code)

            GeniusRegistration.objects.get_or_create(
                competition=comp,
                team=team,
                defaults={'status': 'registered'},
            )
            team_ids.append(team.id)
            self.stdout.write(f'  Team {team.name} ({team.invite_code}) — {team.member_count()} players')

        if options['start'] and team_ids:
            for tid in team_ids:
                team = GeniusTeam.objects.get(pk=tid)
                lock_roster(team.captain, comp.id, tid)
            starter = User.objects.filter(is_staff=True).first()
            if not starter:
                starter = GeniusTeam.objects.get(pk=team_ids[0]).captain
            result = start_competition(comp.id, starter)
            self.stdout.write(self.style.SUCCESS(f'Bracket started: {result}'))

        self.stdout.write(self.style.NOTICE(f'Hub: /dashboard/genius/competition/{comp.id}/'))
