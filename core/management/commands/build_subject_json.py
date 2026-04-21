"""
Management command : build_subject_json
========================================
Réorganise le _pdf_index.json existant (535 PDFs déjà parsés) en fichiers JSON
par matière — instantané, pas de re-parsing.

Usage :
    python manage.py build_subject_json            # tout exporter
    python manage.py build_subject_json --subject maths
    python manage.py build_subject_json --force    # forcer même si JSON existe

Résultat dans database/json/ :
    exams_maths.json, exams_physique.json, exams_svt.json, ...
    chapters_maths.json, chapters_physique.json, ...

Si _pdf_index.json n'existe pas encore : lance d'abord rebuild_pdf_index.
"""
import json
import re
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


# ── Matières ─────────────────────────────────────────────────────────────────
SUBJECTS = [
    'maths', 'physique', 'chimie', 'svt', 'francais', 'philosophie',
    'histoire', 'anglais', 'economie', 'informatique', 'espagnol', 'art',
]

# Mots-clés dans le nom de fichier → matière
FILENAME_SUBJECT_MAP = {
    'maths':        ['maths', 'math', 'mathemat'],
    'physique':     ['physique', 'phys'],
    'chimie':       ['chimie', 'chim'],
    'svt':          ['svt', 'biologie', 'geologie'],
    'francais':     ['francais', 'kreyol', 'creole'],
    'philosophie':  ['philosophie', 'philo'],
    'histoire':     ['histoire', 'hist', 'sciences_sociales'],
    'anglais':      ['anglais', 'english'],
    'economie':     ['economie', 'eco'],
    'informatique': ['informatique', 'info'],
    'espagnol':     ['espagnol'],
    'art':          ['art'],
}

# Mapping fichiers chapitre → matière (basé sur mots dans le nom)
CHAPTER_SUBJECT_MAP = {
    'math':              'maths',
    'physique':          'physique',
    'chimie':            'chimie',
    'biologie':          'svt',
    'geologie':          'svt',
    'economie':          'economie',
    'informatique':      'informatique',
    'anglais':           'anglais',
    'espagnol':          'espagnol',
    'kreyol':            'francais',
    'sciences_sociales': 'histoire',
}

SERIES_KEYWORDS = {'lla': 'LLA', 'ses': 'SES', 'svt': 'SVT', 'smp': 'SMP', 'philo': 'Philo', 'ns4': 'NS4'}

# Synonymes OCR (typos frequents dans les noms de fichiers)
FILENAME_TYPOS = {
    'fhysique': 'physique',  # faute de frappe commune
    'chmie':    'chimie',
    'biogeo':   'svt',
    'bio-geo':  'svt',
}


def _get_true_subject(filename: str) -> str | None:
    """
    Determine la vraie matiere d'un examen depuis son nom de fichier.
    Convention : exam_MATIERE_qualifiant_annee_serie_sujet.pdf
    Le premier segment apres 'exam_' donne la matiere reelle.
    """
    fname = filename.lower().replace('.pdf', '')
    # Extraire le premier segment apres exam_
    m = re.match(r'exam(?:en)?_([^_/\\]+)', fname)
    if not m:
        return None
    segment = m.group(1)  # ex: 'chimie', 'maths', 'svt', 'philosophie'
    # Corrections de typos
    for typo, correct in FILENAME_TYPOS.items():
        if typo in segment:
            segment = segment.replace(typo, correct)
    # Matching sur le premier segment uniquement
    for subject, keywords in FILENAME_SUBJECT_MAP.items():
        if any(kw in segment for kw in keywords):
            return subject
    return None


def _detect_year(filename: str) -> str:
    m = re.search(r'(20\d{2}|19\d{2})', filename)
    return m.group(1) if m else ''


def _detect_series(filename: str) -> list:
    fname = filename.lower()
    return [label for kw, label in SERIES_KEYWORDS.items() if kw in fname]


def _is_chapter_file(filename: str) -> bool:
    """Vrai si c'est un fichier de programme/chapitre (pas un examen)."""
    fname = filename.lower()
    return 'programme' in fname or ('chapter' in fname and 'exam' not in fname)


class Command(BaseCommand):
    help = 'Réorganise _pdf_index.json en fichiers JSON par matière (exams + chapitres)'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='',
                            help='Exporte seulement cette matière.')
        parser.add_argument('--force', action='store_true',
                            help='Force le re-export même si JSON existe.')
        parser.add_argument('--chapters-only', action='store_true')
        parser.add_argument('--exams-only', action='store_true')

    def handle(self, *args, **options):
        db_path = Path(getattr(settings, 'COURSE_DB_PATH', ''))
        if not db_path or not db_path.exists():
            self.stderr.write(self.style.ERROR(f'COURSE_DB_PATH introuvable : {db_path}'))
            return

        index_file = db_path / '_pdf_index.json'
        if not index_file.exists():
            self.stderr.write(self.style.ERROR(
                '_pdf_index.json introuvable.\n'
                'Lance d\'abord : python manage.py rebuild_pdf_index'
            ))
            return

        json_dir = db_path / 'json'
        json_dir.mkdir(exist_ok=True)

        only_subject  = options.get('subject', '').strip().lower()
        force         = options.get('force', False)
        chapters_only = options.get('chapters_only', False)
        exams_only    = options.get('exams_only', False)

        # ── Charger le cache existant ────────────────────────────────────────
        self.stdout.write('\n📂 Lecture du cache _pdf_index.json...')
        t0 = time.time()
        with open(index_file, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        all_files: dict = raw.get('files', {})
        non_empty = {k: v for k, v in all_files.items() if v and v.strip()}
        self.stdout.write(self.style.SUCCESS(
            f'  ✅ {len(non_empty)}/{len(all_files)} fichiers non-vides chargés '
            f'en {round(time.time()-t0, 2)}s'
        ))
        self.stdout.write(f'📁 Sortie : {json_dir}\n')

        # Séparer examens vs programmes
        exam_files    = {k: v for k, v in non_empty.items() if not _is_chapter_file(k)}
        chapter_files = {k: v for k, v in non_empty.items() if _is_chapter_file(k)}
        self.stdout.write(f'  {len(exam_files)} fichiers d\'examens | {len(chapter_files)} fichiers de programme\n')

        total_start = time.time()

        # ── Export examens ────────────────────────────────────────────────────
        if not chapters_only:
            subjects_to_do = (
                [only_subject] if only_subject and only_subject in SUBJECTS
                else SUBJECTS
            )
            for subject in subjects_to_do:
                self._export_exams(subject, exam_files, json_dir, force)

        # ── Export chapitres ──────────────────────────────────────────────────
        if not exams_only:
            self._export_chapters(chapter_files, json_dir, only_subject, force)

        elapsed = round(time.time() - total_start, 1)
        self.stdout.write(self.style.SUCCESS(f'\n✅ Export terminé en {elapsed}s\n'))
        self.stdout.write('💡 Le serveur Django utilisera automatiquement ces JSON au prochain démarrage.\n')

    def _export_exams(self, subject: str, all_exam_files: dict, json_dir: Path, force: bool):
        out_file = json_dir / f'exams_{subject}.json'

        if out_file.exists() and not force:
            self.stdout.write(f'  ⏭  exams_{subject}.json déjà là (--force pour re-export)')
            return

        matched = {}
        for fname, text in all_exam_files.items():
            fname_lower = fname.lower()
            is_exam = ('exam_' in fname_lower or 'examen' in fname_lower)
            if not is_exam:
                continue
            # Classification par prefecture (segment apres exam_) - evite les
            # faux positifs comme 'svt' dans 'exam_chimie_..._svt-smp_...' (serie)
            true_subj = _get_true_subject(fname)
            if true_subj == subject:
                matched[fname] = text
            elif true_subj is None:
                # Fallback : keyword n'importe ou (si format non standard)
                keywords = FILENAME_SUBJECT_MAP.get(subject, [subject])
                if any(kw in fname_lower for kw in keywords):
                    matched[fname] = text

        if not matched:
            self.stdout.write(self.style.WARNING(f'  ⚠️  Aucun examen trouvé pour "{subject}"'))
            return

        exams = []
        total_chars = 0
        for fname in sorted(matched):
            text = matched[fname]
            text = re.sub(r'\n{4,}', '\n\n', text)
            text = re.sub(r' {3,}', '  ', text)
            exams.append({
                'file':   fname,
                'year':   _detect_year(fname),
                'series': _detect_series(fname),
                'chars':  len(text),
                'text':   text,
            })
            total_chars += len(text)

        data = {
            'subject':      subject,
            'generated_at': time.strftime('%Y-%m-%d %H:%M'),
            'total_files':  len(exams),
            'total_chars':  total_chars,
            'exams':        exams,
        }
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

        size_kb = round(out_file.stat().st_size / 1024)
        self.stdout.write(self.style.SUCCESS(
            f'  ✅ exams_{subject}.json — {len(exams)} examens, '
            f'{total_chars:,} chars ({size_kb} KB)'
        ))

    def _export_chapters(self, chapter_files: dict, json_dir: Path, only_subject: str, force: bool):
        if not chapter_files:
            # Try scanning chapter/ folder from db_path
            db_path = Path(getattr(settings, 'COURSE_DB_PATH', ''))
            chapter_dir = db_path / 'chapter'
            if not chapter_dir.exists():
                self.stdout.write('  ℹ️  Aucun fichier de programme détecté dans le cache.')
                return

        self.stdout.write('\n📖 Export programmes...')
        found_any = False

        for fname, text in sorted(chapter_files.items()):
            fname_lower = fname.lower()
            detected = None
            for kw, subj in CHAPTER_SUBJECT_MAP.items():
                if kw in fname_lower:
                    detected = subj
                    break
            if detected is None:
                self.stdout.write(f'    ❓ Matière inconnue : {fname}')
                continue
            if only_subject and detected != only_subject:
                continue

            out_file = json_dir / f'chapters_{detected}.json'
            if out_file.exists() and not force:
                self.stdout.write(f'  ⏭  chapters_{detected}.json déjà là')
                continue

            text = re.sub(r'\n{4,}', '\n\n', text)
            text = re.sub(r' {3,}', '  ', text)
            data = {
                'subject':      detected,
                'generated_at': time.strftime('%Y-%m-%d %H:%M'),
                'file':         fname,
                'chars':        len(text),
                'text':         text,
            }
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

            size_kb = round(out_file.stat().st_size / 1024)
            self.stdout.write(self.style.SUCCESS(
                f'    ✅ chapters_{detected}.json ← {fname} ({size_kb} KB)'
            ))
            found_any = True

        if not found_any and not only_subject:
            self.stdout.write('  ℹ️  Aucun fichier de programme trouvé dans le cache.')
            self.stdout.write('  💡 Les fichiers de programmes sont peut-être dans database/chapter/ '
                              'mais pas encore indexés.')
            self.stdout.write('  👉 Lance: python manage.py rebuild_pdf_index  puis relance cette commande.')
