"""
Management command : fix_cross_subject
========================================
Corrige le problème de classification croisée dans les fichiers exams_*.json.

PROBLÈME :
  Les fichiers nommés exam_chimie_..._svt-smp_... étaient classés à la fois
  dans exams_chimie.json ET exams_svt.json car 'svt' apparaît dans le nom
  (c'est un nom de SÉRIE, pas la matière).

SOLUTION :
  La convention de nommage est toujours : exam_MATIERE_qualifiant_...pdf
  Le premier segment après 'exam_' donne la vraie matière.

Usage :
    python manage.py fix_cross_subject            # analyse + corrige
    python manage.py fix_cross_subject --dry-run  # aperçu sans modifier
"""
import json
import re
from pathlib import Path
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand

SUBJECTS = [
    'maths', 'physique', 'chimie', 'svt', 'francais', 'philosophie',
    'histoire', 'anglais', 'economie', 'informatique', 'espagnol', 'art',
]

# Mots-cles dans le premier segment du nom de fichier → matiere
SEGMENT_MAP = {
    'maths':        ['maths', 'math', 'mathemat'],
    'physique':     ['physique', 'phys', 'fhysique'],  # 'fhysique' = typo OCR
    'chimie':       ['chimie', 'chim', 'chmie'],       # 'chmie' = typo
    'svt':          ['svt', 'biologie', 'biogeo', 'bio-geo', 'geologie'],
    'francais':     ['francais', 'kreyol', 'creole'],
    'philosophie':  ['philosophie', 'philo'],
    'histoire':     ['histoire', 'hist', 'sciences_sociales'],
    'anglais':      ['anglais', 'english'],
    'economie':     ['economie', 'eco'],
    'informatique': ['informatique', 'info'],
    'espagnol':     ['espagnol'],
    'art':          ['art', 'arts'],
}


def _get_true_subject(filename: str) -> str | None:
    """
    Détermine la vraie matière d'un examen depuis son nom de fichier.
    Convention : exam_MATIERE_qualifiant_annee_serie_sujet.pdf
    Le premier segment après 'exam_' ou 'examen_' donne la matière réelle.
    """
    fname = filename.lower().replace('.pdf', '')

    # Extraire le premier segment : exam_SEGMENT_rest
    m = re.match(r'exam(?:en)?_([^_/\\]+)', fname)
    if not m:
        return None

    segment = m.group(1)  # ex: 'chimie', 'maths', 'svt', 'philosophie', etc.

    for subject, keywords in SEGMENT_MAP.items():
        if any(kw in segment for kw in keywords):
            return subject

    return None


class Command(BaseCommand):
    help = 'Corrige la classification des examens dans les fichiers exams_*.json'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Affiche les changements sans modifier les fichiers')
        parser.add_argument('--subject', type=str, default='',
                            help='Traite seulement cette matiere')

    def handle(self, *args, **options):
        db_path = Path(getattr(settings, 'COURSE_DB_PATH', ''))
        if not db_path or not db_path.exists():
            self.stderr.write(self.style.ERROR(f'COURSE_DB_PATH introuvable : {db_path}'))
            return

        json_dir = db_path / 'json'
        if not json_dir.exists():
            self.stderr.write(self.style.ERROR('database/json/ introuvable'))
            return

        dry_run = options.get('dry_run', False)
        only_subject = options.get('subject', '').strip().lower()

        if dry_run:
            self.stdout.write('[DRY-RUN] Aucune modification ne sera effectuee\n')

        subjects_to_do = [only_subject] if only_subject else SUBJECTS

        # --- Etape 1 : charger tous les JSON ---
        all_data = {}  # subject -> data dict
        for s in SUBJECTS:
            path = json_dir / f'exams_{s}.json'
            if path.exists():
                with open(path, encoding='utf-8') as f:
                    all_data[s] = json.load(f)
            else:
                all_data[s] = {'subject': s, 'exams': []}

        # --- Etape 2 : identifier les examens mal places ---
        total_removed = 0
        total_kept = 0
        unknown_files = []

        for s in subjects_to_do:
            data = all_data.get(s)
            if not data:
                continue

            exams = data.get('exams', [])
            keep = []
            removed = []

            for ex in exams:
                fname = ex.get('file', '')
                true_subj = _get_true_subject(fname)

                if true_subj is None:
                    # Impossible de determiner la matiere → on garde (securite)
                    keep.append(ex)
                    unknown_files.append((s, fname))
                elif true_subj == s:
                    keep.append(ex)
                    total_kept += 1
                else:
                    removed.append((fname, true_subj))
                    total_removed += 1

            if removed:
                self.stdout.write(f'\n[{s.upper()}] {len(removed)} examen(s) a retirer :')
                for fname, correct_s in removed[:10]:
                    self.stdout.write(
                        self.style.WARNING(f'  RETIRER  {fname[:65]}')
                    )
                    self.stdout.write(f'           -> appartient a [{correct_s}]')
                if len(removed) > 10:
                    self.stdout.write(f'  ... et {len(removed)-10} autres')

                if not dry_run:
                    data['exams'] = keep
                    data['total_files'] = len(keep)
                    data['total_chars'] = sum(len(e.get('text', '')) for e in keep)

                    path = json_dir / f'exams_{s}.json'
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
                    self.stdout.write(self.style.SUCCESS(
                        f'  [OK] exams_{s}.json mis a jour : {len(keep)} examens restes'
                    ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'[{s.upper()}] OK - {len(exams)} examens, aucun intrus'
                ))

        # --- Etape 3 : rapport final ---
        self.stdout.write(f'\n{"="*60}')
        self.stdout.write(self.style.SUCCESS(
            f'Resultat : {total_removed} examens retires | {total_kept} confirmes'
        ))

        if unknown_files:
            self.stdout.write(f'\nFichiers sans matiere detectee ({len(unknown_files)}) :')
            for s, f in unknown_files[:5]:
                self.stdout.write(f'  [{s}] {f}')

        if dry_run and total_removed > 0:
            self.stdout.write('\n[DRY-RUN] Lance sans --dry-run pour appliquer les corrections.')
        elif not dry_run and total_removed > 0:
            self.stdout.write(
                '\nLes fichiers JSON sont corriges. '
                'Redemarrage du serveur requis pour recharger le cache.'
            )
