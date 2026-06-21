"""
Management command: Générer un bank de quiz depuis les examens JSON.
Usage: python manage.py generate_quiz_bank --subject physique --count 100
"""
import json
import os
import time
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings


SUBJECTS_ALL = ['maths', 'physique', 'chimie', 'svt', 'anglais', 'economie',
                'histoire', 'informatique', 'philosophie', 'francais', 'espagnol']


class Command(BaseCommand):
    help = 'Génère un bank de quiz depuis les examens JSON et le sauvegarde en JSON local'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='physique')
        parser.add_argument('--count', type=int, default=50)
        parser.add_argument('--all', action='store_true', help='Générer pour toutes les matières')

    def handle(self, *args, **options):
        from core import gemini
        db_dir = Path(settings.BASE_DIR) / 'database'

        subjects = SUBJECTS_ALL if options['all'] else [options['subject']]

        for subject in subjects:
            self.stdout.write(f'\n=== Génération quiz pour: {subject} ===')
            count = options['count']

            # Load exam context
            exam_file = db_dir / 'json' / f'exams_{subject}.json'
            if not exam_file.exists():
                self.stdout.write(self.style.WARNING(f'  Pas de fichier exams pour {subject}, skip'))
                continue

            with open(exam_file, encoding='utf-8') as f:
                exam_data = json.load(f)

            # Extract texts from exams
            texts = []
            for exam in exam_data.get('exams', [])[:15]:
                t = exam.get('text', '') or exam.get('text_raw', '')
                if t and len(t) > 100:
                    texts.append(t[:2000])

            if not texts:
                self.stdout.write(self.style.WARNING(f'  Pas de texte exam pour {subject}, skip'))
                continue

            exam_context = '\n\n---\n\n'.join(texts[:5])

            # Load existing bank
            bank_file = db_dir / f'quiz_bank_{subject}.json'
            if bank_file.exists():
                with open(bank_file, encoding='utf-8') as f:
                    bank = json.load(f)
                existing = bank.get('questions', [])
            else:
                bank = {'subject': subject, 'total': 0, 'questions': []}
                existing = []

            existing_enonced = {q.get('enonce', '')[:60] for q in existing}

            all_new = []
            batches = max(1, (count - len(existing)) // 10)

            for i in range(batches):
                self.stdout.write(f'  Batch {i+1}/{batches}...')
                try:
                    new_qs = gemini.generate_quiz_questions(
                        subject, count=10,
                        exam_context=exam_context,
                    )
                    if new_qs:
                        # Deduplicate
                        for q in new_qs:
                            enonce = q.get('enonce', '')[:60]
                            if enonce and enonce not in existing_enonced:
                                all_new.append(q)
                                existing_enonced.add(enonce)
                    time.sleep(0.5)
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  Erreur batch {i+1}: {e}'))
                    break

            # Merge and save
            merged = existing + all_new
            bank['questions'] = merged
            bank['total'] = len(merged)

            with open(bank_file, 'w', encoding='utf-8') as f:
                json.dump(bank, f, ensure_ascii=False, indent=2)

            self.stdout.write(self.style.SUCCESS(
                f'  Sauvegardé: {len(merged)} questions ({len(all_new)} nouvelles) → {bank_file.name}'
            ))
