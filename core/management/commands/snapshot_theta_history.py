"""
Management command: Snapshot quotidien de StudentAbility -> ThetaHistory.
À exécuter chaque nuit via cron ou Celery beat.
"""
from datetime import date
from django.core.management.base import BaseCommand
from core.models import StudentAbility, ThetaHistory
from core.ml.irt_engine import theta_to_display_score


class Command(BaseCommand):
    help = "Snapshot quotidien de StudentAbility vers ThetaHistory pour le calcul de vélocité"

    def handle(self, *args, **options):
        today = date.today()
        count = 0
        abilities = StudentAbility.objects.all().select_related("user")
        for ability in abilities:
            ThetaHistory.objects.update_or_create(
                user=ability.user,
                subject=ability.subject,
                date=today,
                defaults={
                    "theta": ability.theta,
                    "theta_se": ability.theta_se,
                    "display_score": theta_to_display_score(ability.theta),
                },
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"{count} snapshots créés/mis à jour pour la date du {today}."))
