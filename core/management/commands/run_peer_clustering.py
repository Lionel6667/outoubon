"""
Management command: Exécute le clustering k-means des profils d'élèves par série.
"""
from django.core.management.base import BaseCommand
from core.ml.peer_clustering import run_clustering_for_serie


class Command(BaseCommand):
    help = "Exécute le clustering k-means des élèves pour identifier les profils similaires"

    def add_arguments(self, parser):
        parser.add_argument("--serie", type=str, default="SVT", help="Série ciblée (SVT, SMP, SES, LLA)")

    def handle(self, *args, **options):
        serie = options.get("serie", "SVT")
        self.stdout.write(f"Clustering des élèves pour la série {serie}...")
        clusters = run_clustering_for_serie(serie=serie)
        if clusters:
            self.stdout.write(self.style.SUCCESS(f"Clustering terminé avec {len(clusters)} clusters."))
        else:
            self.stdout.write(self.style.WARNING("Pas assez d'élèves enregistrés pour calculer des clusters fiables."))
