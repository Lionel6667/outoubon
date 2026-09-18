"""
Management command: Calibre les paramètres d'items IRT 2PL (difficulty_b, discrimination_a).
"""
from django.core.management.base import BaseCommand
from core.ml.irt_calibration import run_calibration_job


class Command(BaseCommand):
    help = "Calibre les paramètres d'items IRT 2PL à partir de l'historique des quiz et exercices"

    def add_arguments(self, parser):
        parser.add_argument("--subject", type=str, default="", help="Matière spécifique à calibrer")

    def handle(self, *args, **options):
        subject = options.get("subject", "")
        self.stdout.write(f"Lancement de la calibration IRT (subject='{subject}')...")
        updated = run_calibration_job(subject=subject)
        self.stdout.write(self.style.SUCCESS(f"Calibration IRT terminée : {updated} items mis à jour."))
