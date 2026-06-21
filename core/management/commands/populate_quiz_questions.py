"""
Management command : python manage.py populate_quiz_questions

Extrait des QCM depuis les vrais examens PDF et les stocke en BDD.
Les questions dans l'app seront 100% issues des examens officiels.

Usage :
  python manage.py populate_quiz_questions                # toutes les matières
  python manage.py populate_quiz_questions --subject maths
  python manage.py populate_quiz_questions --force        # re-génère tout
  python manage.py populate_quiz_questions --count 20     # 20 questions par matière
"""
import time
from django.core.management.base import BaseCommand
from core import gemini, pdf_loader
from core.models import QuizQuestion

SUBJECTS = {
    'maths':       'Maths',
    'physique':    'Physique',
    'chimie':      'Chimie',
    'svt':         'SVT',
    'philosophie': 'Philosophie',
    'histoire':    'Histoire & Géo',
    'anglais':     'Anglais',
}

# Nombre de questions à générer par batch PDF
BATCH_SIZE = 8
# Nombre de batches par matière (8 x 3 = 24 questions par matière)
BATCHES_PER_SUBJECT = 3


class Command(BaseCommand):
    help = 'Extrait des QCM depuis les examens PDF et les stocke en BDD.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
            help='Supprime et re-génère les questions existantes.')
        parser.add_argument('--subject', type=str, default='',
            help='Une seule matière (ex: maths)')
        parser.add_argument('--count', type=int, default=BATCH_SIZE,
            help=f'Questions par batch (défaut: {BATCH_SIZE})')

    def handle(self, *args, **options):
        force   = options['force']
        only    = options['subject'].lower().strip()
        count   = options['count']

        self.stdout.write('📚 Chargement du cache PDF...')
        pdf_loader._ensure_loaded()
        total_cached = len(pdf_loader._cache)
        self.stdout.write(self.style.SUCCESS(f'  {total_cached} fichiers PDFs en cache'))

        subjects_to_process = [only] if only and only in SUBJECTS else list(SUBJECTS.keys())
        grand_total = 0

        for subj in subjects_to_process:
            label = SUBJECTS[subj]
            existing = QuizQuestion.objects.filter(subject=subj).count()

            if existing >= 15 and not force:
                self.stdout.write(f'  ⏭  {label} : {existing} questions déjà en BDD (--force pour re-génèrer)')
                continue

            if force and existing > 0:
                QuizQuestion.objects.filter(subject=subj).delete()
                self.stdout.write(f'  🗑  {label} : {existing} anciennes questions supprimées')

            self.stdout.write(f'\n🔍 Génération pour {label}...')

            # On génère plusieurs batches depuis différentes parties des examens
            total_created = 0
            for batch_num in range(BATCHES_PER_SUBJECT):
                exam_text = pdf_loader.get_exam_context(subj, max_chars=4000)
                if not exam_text:
                    exam_text = pdf_loader.get_course_context(subj, max_chars=3000)
                if not exam_text:
                    self.stdout.write(self.style.WARNING(f'  ⚠  Aucun PDF pour {label}, batch ignoré'))
                    break

                self.stdout.write(f'  Batch {batch_num + 1}/{BATCHES_PER_SUBJECT} ({len(exam_text)} chars)...')
                t0 = time.time()

                try:
                    questions = gemini.extract_quiz_from_exam_text(exam_text, subj, count)
                    elapsed = time.time() - t0
                    self.stdout.write(f'  → {len(questions)} questions en {elapsed:.1f}s')
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ✗ Erreur IA: {e}'))
                    continue

                # Sauvegarder les questions
                created = 0
                for q in questions:
                    enonce = q.get('enonce', '').strip()
                    if not enonce or len(enonce) < 10:
                        continue
                    options = q.get('options', [])
                    if len(options) < 2:
                        continue
                    # Éviter les doublons exacts
                    if QuizQuestion.objects.filter(subject=subj, enonce=enonce).exists():
                        continue
                    QuizQuestion.objects.create(
                        subject          = subj,
                        enonce           = enonce,
                        options          = options,
                        reponse_correcte = str(q.get('reponse_correcte', 0)),
                        explication      = q.get('explication', ''),
                        sujet            = q.get('sujet', label),
                    )
                    created += 1

                total_created += created
                self.stdout.write(self.style.SUCCESS(f'  ✓ {created} questions sauvegardées'))

                # Pause courte entre batches pour éviter le rate-limiting
                if batch_num < BATCHES_PER_SUBJECT - 1:
                    time.sleep(2)

            grand_total += total_created
            final_count = QuizQuestion.objects.filter(subject=subj).count()
            self.stdout.write(self.style.SUCCESS(
                f'  📊 {label} : {final_count} questions total en BDD'
            ))

        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Terminé ! {grand_total} questions créées au total.'
        ))
        total_db = QuizQuestion.objects.count()
        self.stdout.write(f'📦 Total en BDD : {total_db} questions')
