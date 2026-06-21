"""
Management command : python manage.py extract_chapters

COUP DE GÉNIE COÛT :
  → Analyse les 535 PDFs d'examens UNE SEULE FOIS
  → Extrait tous les chapitres par matière (1 appel IA / matière = 8 appels TOTAL)
  → Stocke tout en BDD SubjectChapter
  → Après ça : ZÉRO appel IA pour la liste des chapitres, jamais

Usage :
  python manage.py extract_chapters              -- extrait les matières manquantes seulement
  python manage.py extract_chapters --force      -- re-extrait toutes les matières
  python manage.py extract_chapters --subject maths   -- une seule matière
"""
from django.core.management.base import BaseCommand
from core import gemini, pdf_loader
from core.models import SubjectChapter

SUBJECTS = {
    'maths':       'Maths',
    'physique':    'Physique',
    'chimie':      'Chimie',
    'svt':         'SVT',
    'philosophie': 'Philosophie',
    'histoire':    'Histoire & Géo',
    'anglais':     'Anglais',
}


class Command(BaseCommand):
    help = 'Extrait les chapitres depuis les PDFs d\'examens et les stocke en BDD (1 appel IA / matière).'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
            help='Re-extrait même les matières déjà présentes en BDD.')
        parser.add_argument('--subject', type=str, default='',
            help='Extrait une seule matière (ex: maths, physique...)')

    def handle(self, *args, **options):
        force   = options['force']
        only    = options['subject'].lower().strip()

        # S'assurer que les PDFs sont chargés
        self.stdout.write('Chargement du cache PDF...')
        pdf_loader._ensure_loaded()
        total_cached = len(pdf_loader._cache)
        self.stdout.write(self.style.SUCCESS(f'  {total_cached} fichiers PDF en cache'))

        subjects_to_process = [only] if only and only in SUBJECTS else list(SUBJECTS.keys())

        total_created = 0
        for subj in subjects_to_process:
            label = SUBJECTS[subj]

            # Vérifier si déjà extrait
            existing = SubjectChapter.objects.filter(subject=subj).count()
            if existing > 0 and not force:
                self.stdout.write(f'  ⏭  {label} : {existing} chapitres déjà en BDD (--force pour re-extraire)')
                continue

            self.stdout.write(f'\n🔍 Extraction de {label}...')

            # Récupérer le contenu des examens pour cette matière
            # On prend un maximum de contenu pour que l'IA détecte tous les thèmes
            exam_text = pdf_loader.get_exam_context(subj, max_chars=7000)
            if not exam_text:
                # Fallback : contenu général
                exam_text = pdf_loader.get_course_context(subj, max_chars=7000)

            if not exam_text:
                self.stdout.write(self.style.WARNING(f'  ⚠  Aucun contenu PDF trouvé pour {label} — chapitres générés depuis les connaissances du modèle'))
                exam_text = f"Programme de Terminale Haïtien en {label}. Génère les chapitres typiques."

            # Extraction depuis le programme officiel Bac Haïti (0 appel API)
            chapters = gemini.extract_chapters_for_subject(subj, exam_text)

            if not chapters:
                self.stdout.write(self.style.ERROR(f'  ✗  Aucun chapitre extrait pour {label}'))
                continue

            # Supprimer les anciens si force
            if force:
                deleted, _ = SubjectChapter.objects.filter(subject=subj).delete()
                if deleted:
                    self.stdout.write(f'  🗑  {deleted} anciens chapitres supprimés')

            # Sauvegarder en BDD
            created = 0
            for c in chapters:
                obj, was_created = SubjectChapter.objects.get_or_create(
                    subject=subj,
                    title=c['title'],
                    defaults={
                        'description':   c.get('description', ''),
                        'exam_excerpts': c.get('exam_excerpt', ''),
                        'order':         c.get('order', 1),
                    }
                )
                if was_created:
                    created += 1

            total_created += created
            self.stdout.write(
                self.style.SUCCESS(f'  ✅ {label} : {created} chapitres créés')
            )

        self.stdout.write('\n' + self.style.SUCCESS(
            f'🎉 Extraction terminée ! {total_created} chapitres créés au total.\n'
            f'   Les cours interactifs sont maintenant disponibles sur /cours/'
        ))
