"""
build_chapter_json — Lit les PDFs du programme officiel Bac Haiti
et construit chapters_{subject}.json dans database/json/.

Les PDFs sont dans database/chapter/ :
  Math--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf
  Biologie--Programme_Detaille--.pdf
  Geologie--Programme_detaille--.pdf
  Chimie--Programme_Detaille--.pdf
  Economie--Programme_Detaille--.pdf
  Espagnol--Programme-detaille--.pdf
  Anglais--Programme-detaille--.pdf
  Kreyol--Programme_detaille--.pdf        (→ francais)
  Physique--Programme_Detaille--.pdf
  Sciences_Sociales--Programme_Detaille--.pdf  (→ histoire)
  Informatique--Programme_detaille--.pdf

Usage:
    py manage.py build_chapter_json
    py manage.py build_chapter_json --subject svt
    py manage.py build_chapter_json --force
"""

import json
import re
import unicodedata
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

# ─── Mapping PDF → (subject, matiere_label) ──────────────────────────────────
PDF_MAP = {
    'Math':              ('maths',        'MATHEMATIQUES'),
    'Physique':          ('physique',     'PHYSIQUE'),
    'Chimie':            ('chimie',       'CHIMIE'),
    'Biologie':          ('svt',          'BIOLOGIE'),
    'Geologie':          ('svt',          'GEOLOGIE'),
    'Economie':          ('economie',     'ECONOMIE'),
    'Espagnol':          ('espagnol',     'ESPAGNOL'),
    'Anglais':           ('anglais',      'ANGLAIS'),
    'Kreyol':            ('francais',     'CRÉOLE / FRANÇAIS'),
    'Sciences_Sociales': ('histoire',     'SCIENCES SOCIALES'),
    'Informatique':      ('informatique', 'INFORMATIQUE'),
}


def _norm(s: str) -> str:
    s2 = s.replace('\ufffd', 'E')
    nfkd = unicodedata.normalize('NFKD', s2)
    return ''.join(c for c in nfkd if not unicodedata.combining(c)).upper()


def _clean(s: str) -> str:
    """Nettoie une chaine : espaces multiples, strips."""
    return re.sub(r'\s+', ' ', s.strip())


def _read_pdf_pages(path: Path) -> list:
    """
    Extrait le texte de chaque page via pdfplumber.
    Retourne [(page_num, text), ...]
    """
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError('pdfplumber manquant : pip install pdfplumber')

    pages = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ''
            if t.strip():
                pages.append((i, t))
    return pages


# ─── Détection de chapitres selon le type de PDF ─────────────────────────────

# Ligne de titre de chapitre  (Math, Physique, Economie)
# ex: page entière = "1\nProbabilité et Statistiques\n1"
_CHAP_TITLE_PAGE = re.compile(
    r'^[ \t]*(\d{1,2})[ \t]*\n[ \t]*(.{4,80})[ \t]*\n[ \t]*\d{1,3}[ \t]*$',
    re.M
)

# Table THEME / SOUS-THEME (Biologie, Géologie, Chimie)
_THEME_HEADER = re.compile(
    r'(?m)^.*(?:THEMES?\s*(?:SOUS[\s\-]TH[E\?]MES?)?'
    r'|TH[E\?]MATIQUE[S\s]'
    r'|UNIT[E\?]\s*\d+'
    r'|THEMATIC\s+DOMAIN)',
    re.I
)

# Numéro de THEME dans une cellule de table  ("1- La nutrition", "Thème 2 : ...")
_THEME_NUM = re.compile(
    r'^[ \t]*(?:TH[E\?]ME\s*)?(\d{1,2})\s*[-:\.]\s*(.{5,})',
    re.I
)


def _split_chapters_math_style(pages: list) -> list:
    """
    Pour Math / Physique / Eco : chapitres séparés par des pages courtes
    contenant uniquement [num] [titre] [num].
    """
    chapters = []
    cur = None

    for pnum, text in pages:
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        # Détecte une page "titre de chapitre" : peu de lignes, première = digit
        if len(lines) <= 5 and lines and lines[0].isdigit():
            # Essaie de trouver un titre dans les lignes suivantes
            title = ''
            for ln in lines[1:]:
                if not ln.isdigit() and len(ln) > 3:
                    title = _clean(ln)
                    break
            if title:
                if cur:
                    chapters.append(cur)
                cur = {
                    'num':   int(lines[0]),
                    'title': title,
                    'pages': [pnum],
                    'text':  '',
                }
                continue

        if cur:
            cur['pages'].append(pnum)
            cur['text'] += '\n' + text

    if cur:
        chapters.append(cur)
    return chapters


def _split_chapters_theme_style(pages: list) -> list:
    """
    Pour Bio / Geo / Chimie :
    - Tente de détecter les chapitres via THEME/sous-sections numérotées
    - Fallback : conserve TOUT le texte en 1 bloc (l'IA structurera ensuite)
    """
    all_text = '\n'.join(t for _, t in pages)
    lines = all_text.split('\n')

    chapters = []
    cur = None
    cur_lines = []
    header_skip = re.compile(
        r'^(?:THEME[S]?|SOUS[\s\-]THEME|COMP[ÉE]TENCES?|CONTENUS?|SUGGESTION)',
        re.I
    )

    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        n = _norm(s)

        # Ignorer les en-têtes de tableau
        if header_skip.match(n):
            continue

        # Section numérotée : "1.", "1-", "2.", "2.1." en début de ligne
        m = re.match(r'^(\d{1,2})(?:\.(\d)?)?[\s\.\-]\s*(.{5,})', s)
        if m and not re.search(r'(?i)comp[ée]tence|contenu', s):
            num_str = m.group(1) + ('.' + m.group(2) if m.group(2) else '')
            title   = _clean(m.group(3))
            if cur:
                cur['text'] = _clean(' '.join(cur_lines))
                chapters.append(cur)
            cur = {
                'num':   float(num_str) if '.' in num_str else int(num_str),
                'title': title,
                'pages': [],
                'text':  '',
            }
            cur_lines = []
            continue

        if cur:
            cur_lines.append(s)

    if cur:
        cur['text'] = _clean(' '.join(cur_lines))
        chapters.append(cur)

    # Fallback : aucune section → 1 seul bloc brut (l'IA le structurera)
    if not chapters:
        chapters = [{
            'num': 1,
            'title': 'Programme complet',
            'pages': [p for p, _ in pages],
            'text': _clean(all_text),
        }]
    return chapters


def _split_chapters_anglais(pages: list) -> list:
    """
    Pour Anglais : garder tout le texte en un bloc brut.
    La structure thématique sera extraite par IA dans parse_chapter_structure.
    """
    all_text = '\n'.join(t for _, t in pages)
    return [{
        'num': 1,
        'title': 'Programme complet',
        'pages': [p for p, _ in pages],
        'text': _clean(all_text),
    }]


def _detect_pdf_style(path_stem: str) -> str:
    """Retourne le style de splitting: 'math', 'theme', 'anglais'."""
    stem = path_stem.lower()
    if any(k in stem for k in ['math', 'physique', 'economie', 'espagnol',
                                'kreyol', 'informatique', 'sciences_sociales']):
        return 'math'
    if any(k in stem for k in ['biologie', 'geologie', 'chimie', 'biologie']):
        return 'theme'
    if 'anglais' in stem:
        return 'anglais'
    return 'math'   # fallback


def process_pdf(path: Path) -> list:
    """
    Extrait et découpe un PDF de programme en chapitres.
    Retourne [{num, title, pages, text}, ...]
    """
    pages = _read_pdf_pages(path)
    style = _detect_pdf_style(path.stem)

    if style == 'theme':
        chapters = _split_chapters_theme_style(pages)
    elif style == 'anglais':
        chapters = _split_chapters_anglais(pages)
    else:
        chapters = _split_chapters_math_style(pages)
        # Fallback si math style n'a rien trouvé
        if not chapters:
            chapters = _split_chapters_theme_style(pages)

    return chapters


class Command(BaseCommand):
    help = 'Construit chapters_{subject}.json depuis les PDFs du programme officiel'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='',
                            help='Ex: svt, maths, chimie...')
        parser.add_argument('--force', action='store_true',
                            help='Re-génère même si JSON existe')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        db_path  = Path(getattr(settings, 'COURSE_DB_PATH', ''))
        chap_dir = db_path / 'chapter'
        json_dir = db_path / 'json'
        json_dir.mkdir(exist_ok=True)

        only     = options['subject'].strip().lower()
        force    = options['force']
        dry_run  = options['dry_run']

        if not chap_dir.exists():
            self.stderr.write(f'Dossier chapter introuvable : {chap_dir}')
            return

        # Trouver tous les PDFs dans database/chapter/ (incluant sous-dossiers)
        all_pdfs = list(chap_dir.glob('**/*.pdf')) + list(chap_dir.glob('*.pdf'))
        all_pdfs = list({p.resolve() for p in all_pdfs})  # dédupliquer

        if not all_pdfs:
            self.stderr.write(f'Aucun PDF trouvé dans {chap_dir}')
            return

        self.stdout.write(f'\n{len(all_pdfs)} PDFs trouvés dans {chap_dir}\n')

        # Grouper par sujet
        subject_data: dict[str, list] = {}
        subject_pdf_names: dict[str, list] = {}

        for pdf in sorted(all_pdfs):
            key    = None
            matiere = None
            stem   = pdf.stem   # ex: "Math--Programme_Detaille--4eme_annee_..."
            stem_lower = stem.lower()

            for prefix, (subj, mat) in PDF_MAP.items():
                if stem.startswith(prefix) or prefix.lower() in stem_lower:
                    key    = subj
                    matiere = mat
                    break

            if not key:
                self.stdout.write(f'  [IGNORE] {pdf.name} — sujet non reconnu')
                continue
            if only and key != only:
                continue

            if key not in subject_data:
                subject_data[key]      = []
                subject_pdf_names[key] = []
            subject_pdf_names[key].append(pdf.name)

            self.stdout.write(f'  Lecture [{key.upper()} / {matiere}] {pdf.name}...')
            try:
                chapters = process_pdf(pdf)
                for ch in chapters:
                    ch['matiere'] = matiere
                    ch['source_pdf'] = pdf.name
                subject_data[key].extend(chapters)
                self.stdout.write(f'    → {len(chapters)} chapitres extraits')
            except Exception as e:
                self.stdout.write(f'    ERREUR : {e}')

        # Sauvegarder par sujet
        total_chap = 0
        for subj, chapters in subject_data.items():
            out_file = json_dir / f'chapters_{subj}.json'
            if out_file.exists() and not force and not dry_run:
                self.stdout.write(f'  [SKIP] chapters_{subj}.json existe (--force pour écraser)')
                continue

            # Renumeroter
            for i, ch in enumerate(chapters, 1):
                if not ch.get('num'):
                    ch['num'] = i

            total_chap += len(chapters)

            if dry_run:
                self.stdout.write(f'  [DRY-RUN] chapters_{subj}.json → {len(chapters)} chapitres')
                for ch in chapters:
                    self.stdout.write(
                        f'    #{ch["num"]:02d} [{ch["matiere"]}] {ch["title"][:60]}'
                        f'  ({len(ch.get("text",""))} chars)'
                    )
                continue

            data = {
                'subject':       subj,
                'total_chapters': len(chapters),
                'source_pdfs':   subject_pdf_names.get(subj, []),
                'chapters':      chapters,
            }
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.stdout.write(f'  SAUVEGARDE chapters_{subj}.json — {len(chapters)} chapitres')

        flag = ' [DRY-RUN]' if dry_run else ''
        self.stdout.write(f'\nRESULTAT{flag}: {total_chap} chapitres au total')
