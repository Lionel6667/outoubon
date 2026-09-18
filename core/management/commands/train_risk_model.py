"""
Management command: Entraîne ou met à jour le modèle prédictif de risque BAC.
"""
from django.core.management.base import BaseCommand
from accounts.models import UserProfile
from core.ml.risk_predictor import predict_risk


class Command(BaseCommand):
    help = "Calcule ou recalibre les prédictions de risque BAC pour les élèves actifs"

    def handle(self, *args, **options):
        profiles = UserProfile.objects.all().select_related("user")
        cnt = 0
        for p in profiles:
            if p.user:
                predict_risk(p.user, p)
                cnt += 1
        self.stdout.write(self.style.SUCCESS(f"Prédictions de risque actualisées pour {cnt} élèves."))
