"""
Management command: Générer les fichiers de notes de cours depuis les chapters JSON.
Usage: python manage.py generate_course_notes --subject chimie
       python manage.py generate_course_notes --all
"""
import json
import re
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings


SUBJECT_MAP = {
    'chimie': ('note_chimie.json', 'CHAPITRE'),
    'anglais': ('note_anglais.json', 'CHAPITRE'),
    'economie': ('note_economie.json', 'CHAPITRE'),
    'histoire': ('note_histoire.json', 'CHAPITRE'),
    'informatique': ('note_informatique.json', 'CHAPITRE'),
    'philosophie': ('note_philosophie.json', 'CHAPITRE'),
    'espagnol': ('note_espagnol.json', 'CHAPITRE'),
    'francais': ('note_francais.json', 'KONPETANS'),
}


def clean_ocr_text(text):
    """Nettoie les artefacts OCR courants."""
    text = re.sub(r'(\w)0on\b', r'\1tion', text)
    text = re.sub(r'(\w)0ons\b', r'\1tions', text)
    text = re.sub(r'\b0on\b', 'tion', text)
    text = re.sub(r'[0-9](?=[a-z]{2,})', '', text)
    text = re.sub(r' {3,}', '  ', text)
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


class Command(BaseCommand):
    help = 'Génère les fichiers note_*.json depuis les chapters JSON'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='chimie')
        parser.add_argument('--all', action='store_true')

    def handle(self, *args, **options):
        db_dir = Path(settings.BASE_DIR) / 'database'

        subjects = list(SUBJECT_MAP.keys()) if options['all'] else [options['subject']]

        for subject in subjects:
            if subject not in SUBJECT_MAP:
                self.stdout.write(self.style.ERROR(f'Sujet inconnu: {subject}'))
                continue

            note_file, chapter_prefix = SUBJECT_MAP[subject]
            chapters_file = db_dir / 'json' / f'chapters_{subject}.json'

            if not chapters_file.exists():
                self.stdout.write(self.style.WARNING(f'Pas de chapters_{subject}.json, skip'))
                continue

            with open(chapters_file, encoding='utf-8') as f:
                data = json.load(f)

            chapters = data.get('chapters', [])
            if not chapters:
                self.stdout.write(self.style.WARNING(f'Pas de chapitres dans {chapters_file.name}'))
                continue

            parts = []
            for ch in chapters:
                num = ch.get('num', '')
                title = ch.get('title', f'Chapitre {num}')
                text = clean_ocr_text(ch.get('text', '') or '')

                header = f'{chapter_prefix} {num} — {title}'
                if text:
                    parts.append(f'{header}\n\n{text}')
                else:
                    parts.append(f'{header}\n\nContenu à compléter.')

            raw_text = '\n\n══════════════════════════════════════\n\n'.join(parts)

            out_file = db_dir / note_file
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump({'raw_text': raw_text}, f, ensure_ascii=False, indent=2)

            self.stdout.write(self.style.SUCCESS(f'Créé: {note_file} ({len(chapters)} chapitres)'))
