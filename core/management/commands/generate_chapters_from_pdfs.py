"""
Management command : python manage.py generate_chapters_from_pdfs

Lit les PDFs du programme officiel dans database/chapter/,
les envoie à l'IA et génère les chapitres structurés pour chaque matière.

L'IA reçoit le vrai programme officiel haïtien et génère les chapitres
avec titres, descriptions, compétences et contenus.

Usage :
  python manage.py generate_chapters_from_pdfs
  python manage.py generate_chapters_from_pdfs --force
  python manage.py generate_chapters_from_pdfs --subject svt
"""
import json
import re
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings

from core import gemini
from core.models import SubjectChapter

# Mapping : sujet → {subsection → [pdfs]}  ('' = pas de sous-section)
CHAPTER_PDF_MAP = {
    'maths':    {'': ['Math--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf']},
    'physique': {'': ['Physique--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf']},
    'chimie':   {'': ['Chimie--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf']},
    'svt': {
        'biologie': ['Biologie--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf'],
        'geologie': ['Geologie--Programme_detaille--4eme_annee_Nouveau_Secondaire.pdf'],
    },
    'histoire':    {'': ['Sciences_Sociales--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf']},
    'anglais':     {'': ['Anglais--Programme-detaille--4e_annee_Nouveau_Secondaire.pdf']},
    # philosophie : PDF à ajouter plus tard
}

SUBJECT_LABELS = {
    'maths':       'Mathématiques',
    'physique':    'Physique',
    'chimie':      'Chimie',
    'svt':         'SVT',
    'histoire':    'Histoire & Sciences Sociales',
    'anglais':     'Anglais',
    'philosophie': 'Philosophie',
}

# Labels pour les sous-sections SVT
SUBSECTION_LABELS = {
    'biologie': 'Biologie',
    'geologie': 'Géologie',
}


def _read_pdf(path: Path) -> str:
    """Extrait tout le texte d'un PDF avec pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return '\n'.join(text_parts)
    except Exception as e:
        return f'[Erreur lecture PDF {path.name}: {e}]'


def _generate_chapters_from_programme(programme_text: str, subject: str, label: str) -> list:
    """
    Envoie le texte du programme à l'IA et obtient les chapitres structurés.
    Retourne [{title, description, competences, order}, ...]
    """
    prompt = (
        f"Tu es expert du programme officiel du Bac Haïti (4ème année Nouveau Secondaire - Terminale).\n\n"
        f"Voici le programme officiel complet de {label} :\n\n"
        f"{programme_text[:5000]}\n\n"
        f"Analyse ce programme et génère la liste COMPLÈTE des chapitres à maîtriser pour le Bac.\n\n"
        "Pour chaque chapitre, fournis :\n"
        "- title : titre du chapitre (court, précis)\n"
        "- description : résumé de 2-3 phrases expliquant ce qu'on apprend et pourquoi c'est important\n"
        "- competences : liste des 3-5 compétences clés à acquérir (ce que l'élève doit savoir FAIRE)\n"
        "- contenus : liste des 3-5 points de contenu principaux (les notions abordées)\n"
        "- order : numéro d'ordre (1, 2, 3...)\n\n"
        "IMPORTANT : Base-toi UNIQUEMENT sur le programme fourni. "
        "Regroupe les sous-thèmes connexes en chapitres cohérents.\n\n"
        "Réponds UNIQUEMENT avec ce JSON array :\n"
        '[{"title":"...","description":"...","competences":["...","..."],'
        '"contenus":["...","..."],"order":1}]\n\n'
        "Génère TOUS les chapitres du programme (généralement 6-12 chapitres)."
    )
    text = gemini._call(prompt, max_tokens=3000)
    text = re.sub(r'```[a-z]*\s*', '', text).strip()
    m = re.search(r'\[[\s\S]+\]', text)
    if not m:
        return []
    try:
        chapters = json.loads(m.group(0))
        return chapters if isinstance(chapters, list) else []
    except Exception:
        # Fallback : extraire objet par objet
        chapters = []
        for obj_m in re.finditer(r'\{[^{}]+\}', m.group(0)):
            try:
                obj = json.loads(obj_m.group(0))
                if 'title' in obj:
                    chapters.append(obj)
            except Exception:
                pass
        return chapters


class Command(BaseCommand):
    help = 'Génère les chapitres depuis les PDFs du programme officiel pour chaque matière.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
            help='Re-génère même si les chapitres existent')
        parser.add_argument('--subject', type=str, default='',
            help='Ne traite qu\'une matière (ex: maths, svt...)')

    def handle(self, *args, **options):
        force    = options['force']
        only     = options['subject'].lower().strip()
        db_path  = Path(getattr(settings, 'COURSE_DB_PATH', '')) / 'chapter'

        if not db_path.exists():
            self.stdout.write(self.style.ERROR(f'Dossier chapter introuvable : {db_path}'))
            return

        subjects_to_process = [only] if only and only in CHAPTER_PDF_MAP else list(CHAPTER_PDF_MAP.keys())
        grand_total = 0

        for subj in subjects_to_process:
            subj_label       = SUBJECT_LABELS.get(subj, subj)
            subsection_map   = CHAPTER_PDF_MAP[subj]   # {subsection: [pdfs]}

            self.stdout.write(f'\n📚 {subj_label}')

            for subsection, pdfs in subsection_map.items():
                sub_label = f'{subj_label} — {SUBSECTION_LABELS.get(subsection, subsection)}' if subsection else subj_label

                existing = SubjectChapter.objects.filter(subject=subj, subsection=subsection).count()

                if existing > 0 and not force:
                    self.stdout.write(
                        f'  ⏭  {sub_label} : {existing} chapitres en BDD (--force pour re-générer)'
                    )
                    continue

                if force and existing > 0:
                    SubjectChapter.objects.filter(subject=subj, subsection=subsection).delete()
                    self.stdout.write(f'  🗑  {sub_label} : {existing} anciens chapitres supprimés')

                # Lire les PDFs pour cette (matière, sous-section)
                full_text = ''
                for pdf_name in pdfs:
                    pdf_path = db_path / pdf_name
                    if not pdf_path.exists():
                        self.stdout.write(self.style.WARNING(f'  ⚠  PDF introuvable : {pdf_name}'))
                        continue
                    self.stdout.write(f'  📄 Lecture de {pdf_name}...')
                    pdf_text = _read_pdf(pdf_path)
                    full_text += f'\n\n=== {pdf_name} ===\n{pdf_text}'
                    self.stdout.write(f'     {len(pdf_text)} caractères extraits')

                if not full_text.strip():
                    self.stdout.write(self.style.WARNING(f'  ⚠  Aucun texte extrait pour {sub_label}'))
                    continue

                self.stdout.write(f'\n🤖 Génération des chapitres pour {sub_label}...')
                t0 = time.time()

                try:
                    chapters = _generate_chapters_from_programme(full_text, subj, sub_label)
                    elapsed = time.time() - t0
                    self.stdout.write(f'  → {len(chapters)} chapitres générés en {elapsed:.1f}s')
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ✗ Erreur IA : {e}'))
                    continue

                if not chapters:
                    self.stdout.write(self.style.ERROR(f'  ✗ Aucun chapitre généré pour {sub_label}'))
                    continue

                # Sauvegarder en BDD avec subsection
                created = 0
                for i, chap in enumerate(chapters):
                    title = chap.get('title', '').strip()
                    if not title:
                        continue
                    competences = chap.get('competences', [])
                    contenus    = chap.get('contenus', [])
                    description = chap.get('description', '')
                    if competences:
                        description += '\n\nCompétences :\n' + '\n'.join(f'• {c}' for c in competences)
                    if contenus:
                        description += '\n\nContenus :\n' + '\n'.join(f'• {c}' for c in contenus)

                    SubjectChapter.objects.update_or_create(
                        subject=subj,
                        subsection=subsection,
                        title=title,
                        defaults={
                            'description':   description,
                            'order':         chap.get('order', i + 1),
                            'exam_excerpts': '',
                        }
                    )
                    created += 1

                grand_total += created
                self.stdout.write(self.style.SUCCESS(f'  ✓ {created} chapitres sauvegardés pour {sub_label}'))
                time.sleep(1)

        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Terminé ! {grand_total} chapitres créés au total.'
        ))
