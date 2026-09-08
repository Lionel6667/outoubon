"""
Synchronise le concours général hebdomadaire.

À lancer via cron (ex. chaque heure) :
  python manage.py sync_weekly_genius

Dimanche → ouvre les inscriptions.
Lundi → ferme les inscriptions (roster_locked).
"""
from django.core.management.base import BaseCommand

from core.genius.models import GeniusRegistration
from core.genius.schedule import ensure_weekly_competition, is_registration_open


class Command(BaseCommand):
    help = 'Crée/met à jour le concours général de la semaine (inscriptions dimanche, fermeture lundi).'

    def handle(self, *args, **options):
        comp = ensure_weekly_competition()
        open_reg = is_registration_open()
        auto_locked = 0
        if comp.status == 'roster_locked' and not open_reg:
            # Verrouille automatiquement les équipes inscrites non lockées
            qs = GeniusRegistration.objects.filter(competition=comp, status='registered')
            auto_locked = qs.update(status='roster_locked')
        self.stdout.write(self.style.SUCCESS(
            f'Concours #{comp.pk} « {comp.name} » status={comp.status} '
            f'registration_open={open_reg} auto_locked={auto_locked}'
        ))
