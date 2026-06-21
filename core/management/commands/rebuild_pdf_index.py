"""
Management command : python manage.py rebuild_pdf_index

Utilité :
  - PREMIÈRE INSTALLATION : lance ceci pour indexer les 535 PDFs en JSON
  - APRÈS AJOUT DE NOUVEAUX PDFs : relance pour mettre à jour le cache
  - Le cache JSON (_pdf_index.json) est ensuite chargé en < 1 seconde à chaque démarrage

Usage :
  python manage.py rebuild_pdf_index
  python manage.py rebuild_pdf_index --force   (efface l'ancien cache avant)
"""
from django.core.management.base import BaseCommand
from core import pdf_loader


class Command(BaseCommand):
    help = 'Indexe tous les PDFs dans database/ et sauvegarde le cache JSON.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Efface le cache existant avant de re-indexer.',
        )

    def handle(self, *args, **options):
        if options['force']:
            cache_path = pdf_loader._get_index_cache_path()
            if cache_path.exists():
                cache_path.unlink()
                self.stdout.write(self.style.WARNING(f'Cache supprimé : {cache_path}'))

        self.stdout.write('⏳ Indexage des PDFs en cours (peut prendre 30-60s la première fois)...')
        count = pdf_loader.rebuild_pdf_index()
        self.stdout.write(
            self.style.SUCCESS(
                f'✅ {count} fichiers indexés. Cache JSON sauvegardé.\n'
                f'   Les prochains démarrages du serveur seront instantanés.'
            )
        )
