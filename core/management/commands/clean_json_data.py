"""
Nettoie tous les JSON pédagogiques (local, sans API).

Usage:
    python manage.py clean_json_data
    python manage.py clean_json_data --chapters-only
    python manage.py clean_json_data --exams-only
    python manage.py clean_json_data --exo-only
    python manage.py clean_json_data --subject informatique
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.json_cleaner import (
    clean_chapters_data,
    clean_exams_data,
    clean_exo_file,
    file_size_mb,
    write_json,
)


class Command(BaseCommand):
    help = 'Nettoie chapters_*.json, exams_*.json et exo_*.json (local, sans API)'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='',
                            help='Limiter à une matière (ex: informatique)')
        parser.add_argument('--chapters-only', action='store_true')
        parser.add_argument('--exams-only', action='store_true')
        parser.add_argument('--exo-only', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        import json

        db_path = Path(getattr(settings, 'COURSE_DB_PATH', settings.BASE_DIR / 'database'))
        json_dir = db_path / 'json'
        subject_filter = options['subject'].strip().lower()
        dry = options['dry_run']

        do_chapters = not options['exams_only'] and not options['exo_only']
        do_exams = not options['chapters_only'] and not options['exo_only']
        do_exo = not options['chapters_only'] and not options['exams_only']

        if do_chapters:
            self.stdout.write('\n=== chapters_*.json ===')
            for path in sorted(json_dir.glob('chapters_*.json')):
                subj = path.stem.replace('chapters_', '')
                if subject_filter and subj != subject_filter:
                    continue
                before = file_size_mb(path)
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                n_before = len(data.get('chapters', []))
                lens_before = [len(c.get('text', '')) for c in data.get('chapters', [])]

                cleaned = clean_chapters_data(data)
                n_after = len(cleaned['chapters'])
                lens_after = [len(c.get('text', '')) for c in cleaned['chapters']]

                if dry:
                    self.stdout.write(
                        f'  [DRY] {path.name}: {n_before}→{n_after} ch, '
                        f'{before:.2f}MB, lens {lens_before[:4]}→{lens_after[:4]}'
                    )
                    continue

                write_json(path, cleaned)
                after = file_size_mb(path)
                self.stdout.write(self.style.SUCCESS(
                    f'  OK {path.name}: {n_before}→{n_after} chapitres, '
                    f'{before:.2f}MB → {after:.2f}MB'
                ))

        if do_exams:
            self.stdout.write('\n=== exams_*.json ===')
            for path in sorted(json_dir.glob('exams_*.json')):
                subj = path.stem.replace('exams_', '')
                if subject_filter and subj != subject_filter:
                    continue
                before = file_size_mb(path)
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                n = len(data.get('exams', []))
                rebuilt = sum(1 for e in data.get('exams', []) if e.get('rebuilt'))

                if dry:
                    self.stdout.write(f'  [DRY] {path.name}: {n} exams, {rebuilt} rebuilt, {before:.2f}MB')
                    continue

                cleaned = clean_exams_data(data)
                write_json(path, cleaned)
                after = file_size_mb(path)
                parts_removed = sum(
                    1 for e in cleaned['exams']
                    if e.get('parts_source') == 'removed_after_rebuild'
                )
                self.stdout.write(self.style.SUCCESS(
                    f'  OK {path.name}: {n} exams, parts retirés={parts_removed}, '
                    f'{before:.2f}MB → {after:.2f}MB'
                ))

        if do_exo:
            self.stdout.write('\n=== exo_*.json ===')
            for path in sorted(db_path.glob('exo_*.json')):
                subj = path.stem.replace('exo_', '')
                if subject_filter and subj != subject_filter:
                    continue
                before = file_size_mb(path)
                raw = path.read_bytes()
                # Détection encodage
                for enc in ('utf-8', 'utf-8-sig', 'cp1252', 'latin-1'):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        text = None
                if text is None:
                    text = raw.decode('utf-8', errors='replace')

                import json as _json
                data = _json.loads(text)

                if dry:
                    self.stdout.write(f'  [DRY] {path.name}: {before:.2f}MB enc={enc}')
                    continue

                cleaned = clean_exo_file(data)
                write_json(path, cleaned)
                after = file_size_mb(path)
                self.stdout.write(self.style.SUCCESS(
                    f'  OK {path.name}: enc={enc}, {before:.2f}MB → {after:.2f}MB'
                ))

            # equation_chimie.json aussi
            eq_path = db_path / 'equation_chimique.json'
            if eq_path.exists() and (not subject_filter or subject_filter == 'chimie'):
                before = file_size_mb(eq_path)
                with open(eq_path, encoding='utf-8', errors='replace') as f:
                    data = json.load(f)
                if not dry:
                    write_json(eq_path, clean_exo_file(data))
                    self.stdout.write(self.style.SUCCESS(
                        f'  OK equation_chimique.json: {before:.2f}MB → {file_size_mb(eq_path):.2f}MB'
                    ))

        self.stdout.write(self.style.SUCCESS('\nNettoyage terminé.'))
