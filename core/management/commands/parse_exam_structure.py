"""
parse_exam_structure - Structure les examens en JSON propre (100% regex, sans IA).

Gere :
  - Caracteres OCR corrompus : \ufffd (remplacement) et \uf0b7/\uf0a7 (Symbol font bullets)
  - Themes avec ou sans numero ("Theme : X", "Theme I : X")
  - Questions en bullet, tiret, numerotees
  - Sections PREMIERE/DEUXIEME PARTIE avec accents corrompus

Usage:
    py manage.py parse_exam_structure --all
    py manage.py parse_exam_structure --subject svt
    py manage.py parse_exam_structure --all --redo
    py manage.py parse_exam_structure --subject svt --dry-run --limit 5
"""

import json
import os
import re
import unicodedata

from django.core.management.base import BaseCommand

JSON_DIR = os.path.join('database', 'json')

SUBJECTS = [
    'anglais', 'art', 'chimie', 'economie', 'espagnol',
    'francais', 'histoire', 'informatique', 'maths',
    'philosophie', 'physique', 'svt',
]


# ═══════════════════════════════════════════════════════════════════════════════
# Normalisation
# ═══════════════════════════════════════════════════════════════════════════════

def _norm(s: str) -> str:
    """
    Normalise un texte pour le matching :
    - Retire les accents (é→e, è→e, à→a, etc.)
    - Remplace \ufffd (car. remplacement OCR) par 'E' (approximation la plus courante)
    - Met en majuscules
    """
    # \ufffd represente souvent une lettre accentuee (é, è, ê, ç, etc.)
    # On le remplace par 'E' pour les cas courants (GÉOLOGIE, THÈME, PREMIÈRE...)
    s2 = s.replace('\ufffd', 'E')
    nfkd = unicodedata.normalize('NFKD', s2)
    return ''.join(c for c in nfkd if not unicodedata.combining(c)).upper()


def _fix_ocr(s: str) -> str:
    """Corrige les erreurs OCR visuelles courantes."""
    # "F . 2" ou "F 2" -> "F2" (generation)
    s = re.sub(r'\bF\s*\.\s*(?=\d)', 'F', s)
    s = re.sub(r'\bF\s+(\d)\b', r'F\1', s)
    # Espaces multiples
    s = re.sub(r'[ \t]{2,}', ' ', s)
    return s.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# Patterns — tous appliques au texte NORMALISE (sauf questions)
# ═══════════════════════════════════════════════════════════════════════════════

# Fin de l'entete MENFP (cherche dans texte normalise)
_HEADER_END_N = re.compile(
    r'\n[ \t]*(?:BIOLOGIE|GEOLOGIE|MATHEMATIQUES?|MATHS\b|PHYSIQUE|CHIMIE'
    r'|FRANEAIS|FRANCAIS|PHILOSOPHIE|HISTOIRE|GEOGRAPHIE|ANGLAIS|ENGLISH'
    r'|ECONOMIE|INFORMATIQUE|ART[S\b]|SVT'
    r'|PREMIERE\s+PARTIE|DEUXIEME\s+PARTIE)',
    re.I
)

# Noms de matieres (cherche dans texte normalise)
# \ufffd -> 'E' dans _norm(), donc GEOLOGIE ok, FRANEAIS pour FRANÇAIS
_MATIERES_N = [
    ('BIOLOGIE',       r'BIOLOGIE'),
    ('GEOLOGIE',       r'G[E.]OLOGIE'),        # GÉOLOGIE → GEOLOGIE ou GOOLOGIE
    ('MATHEMATIQUES',  r'MATH[E.]MATIQUES?|MATHS\b'),
    ('PHYSIQUE',       r'PHYSIQUE'),
    ('CHIMIE',         r'CHIMIE'),
    ('FRANCAIS',       r'FRAN[CE]AIS'),         # FRANÇAIS → FRANCAIS ou FRANEAIS
    ('PHILOSOPHIE',    r'PHILOSOPHIE'),
    ('HISTOIRE',       r'HISTOIRE|G[E.]OGRAPHIE'),
    ('ANGLAIS',        r'ANGLAIS|ENGLISH'),
    ('ECONOMIE',       r'[E.]CONOMIE'),          # ÉCONOMIE → ECONOMIE ou EECONOMIE
    ('ART',            r'ARTS?\s+PLASTIQUES?|^ART\b'),
    ('INFORMATIQUE',   r'INFORMATIQUE'),
    ('SVT',            r'SVT|SCIENCES?\s+DE\s+LA\s+VIE'),
]

# Separateurs de partie (cherche dans texte normalise)
_PARTIE_N = re.compile(
    r'(?m)^[ \t]*(?:[A-C][\s\.\-]+)?'
    r'(PREMIERE?|DEUXIEME?|TROISIEME?|QUATRIEME?)[ \t]+PARTIE'
    r'(?:[ \t]*[\(\[]?[ \t]*(\d+)[ \t]*(?:POINTS?|PTS?)[ \t]*[\)\]]?)?',
    re.I
)

# Themes (cherche dans texte normalise)
# Supporte : "THEME : TITRE", "THEME I : TITRE", "THEME 1 : TITRE"
_THEME_N = re.compile(
    r'^[ \t]*TH[E.]?ME\s*(?:([IVX0-9]+)\s*)?[:\.\-]\s*(.+)',
    re.I
)

# Points par question
_PTS_Q = re.compile(
    r'(\d+)\s*(?:pts?|points?)\s*/\s*(?:bonne\s+r[e.]ponse?|question)',
    re.I
)

# Total de points d'une section/matiere
_PTS_TOT = re.compile(r'[\(\[]\s*(\d+)\s*(?:pts?|points?)\s*[\)\]]', re.I)
_PTS_MAT = re.compile(r':\s*(\d+)\s*(?:POINTS?|PTS?)', re.I)

# Marqueur QUESTIONS (cherche dans texte normalise)
_Q_INTRO_N = re.compile(r'^[ \t]*QUESTIONS?\s*[\d\s]*[:\.]?\s*', re.I)

# Contexte TEXTE / SOURCE / PROBLEME
_CTX_N = re.compile(r'^[ \t]*(?:TEXTE?|SOURCE|REFERENCE|PROBL[E.]ME)\s*:', re.I)

# Sous-sections I-, II-, III-
_SUBSEC_N = re.compile(r'^[ \t]*(I{1,3}V?|VI{0,3}|IX|X|[IVX]+)\s*[-\.]\s*(.{3,})', re.I)


# ═══════════════════════════════════════════════════════════════════════════════
# Detecteurs de questions (appliques au texte ORIGINAL)
# ═══════════════════════════════════════════════════════════════════════════════

# Bullets Symbol-font (\uf0b7, \uf0a7) + bullets Unicode + carre plein
_BULLET_CHARS = '\u2022\u00b7\u25b6\u25aa\uf0b7\uf0a7\uf0a8\uf076\uf0de\uf0fc'
_Q_BULLET  = re.compile(rf'^[ \t]*[{re.escape(_BULLET_CHARS)}]\s+(.+)')
_Q_DASH    = re.compile(r'^[ \t]*[-\u2013]\s+(.{4,})')
_Q_NUM     = re.compile(r'^[ \t]*(\d{1,2})\s*[-\.\)\s]\s*(.{5,})')
_Q_LETTER  = re.compile(r'^[ \t]*([a-e])\)\s+(.+)')

# Ligne tres courte (numero seul comme "2" sur une ligne -> continuation)
_STANDALONE_NUM = re.compile(r'^[ \t]*\d{1,2}[ \t]*$')


# ═══════════════════════════════════════════════════════════════════════════════
# Utilitaires
# ═══════════════════════════════════════════════════════════════════════════════

def _pts_q(line: str) -> int:
    m = _PTS_Q.search(line)
    return int(m.group(1)) if m else 0


def _pts_tot(line: str) -> int:
    m = _PTS_TOT.search(line)
    return int(m.group(1)) if m else 0


def _original_title(raw_line: str) -> str:
    """Extrait le titre (apres ':' ou '-') depuis la ligne originale."""
    for sep in [':', '-', '.']:
        pos = raw_line.find(sep, 4)   # skip first 4 chars (Theme...)
        if pos > 0:
            return _fix_ocr(raw_line[pos + 1:])
    return _fix_ocr(raw_line)


def _drop_pts_from(s: str) -> str:
    """Retire les mentions de points d'un titre."""
    s = _PTS_Q.sub('', s)
    s = _PTS_TOT.sub('', s)
    return re.sub(r'\s+', ' ', s).strip(' .-()[]')


# ═══════════════════════════════════════════════════════════════════════════════
# Parsing
# ═══════════════════════════════════════════════════════════════════════════════

def _strip_header(text: str) -> str:
    """Retire l'entete administratif MENFP."""
    norm = _norm(text)
    m = _HEADER_END_N.search(norm)
    if m and m.start() > 30:
        return text[m.start():].strip()
    return text.strip()


def _split_matieres(text: str) -> list:
    """
    Decoupe le corps de l'examen par matieres.
    Retourne [(nom, texte_original), ...]
    """
    norm = _norm(text)
    hits = []

    for name, pat in _MATIERES_N:
        rx = re.compile(r'(?m)^[ \t]*(?:' + pat + r')[ \t]*(?::[^\n]*)?\s*\n', re.I)
        m = rx.search(norm)
        if m:
            hits.append((m.start(), name))

    if not hits:
        return [('GENERAL', text)]

    hits.sort(key=lambda x: x[0])
    seen = set()
    unique = []
    for pos, name in hits:
        if name not in seen:
            seen.add(name)
            unique.append((pos, name))

    result = []
    for i, (pos, name) in enumerate(unique):
        end = unique[i + 1][0] if i + 1 < len(unique) else len(text)
        result.append((name, text[pos:end]))
    return result


def _split_parties(text: str) -> list:
    """
    Decoupe un bloc matiere en PREMIERE PARTIE / DEUXIEME PARTIE / etc.
    Retourne [(label_original, contenu_original), ...]
    """
    norm = _norm(text)
    matches = list(_PARTIE_N.finditer(norm))
    if not matches:
        return [('SECTION', text)]

    result = []
    for i, m in enumerate(matches):
        end  = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # Recuperer le label depuis le texte ORIGINAL (garde les accents)
        lbl  = text[m.start():m.end()].strip()
        body = text[m.end():end]
        result.append((lbl, body))
    return result


def _parse_questions(lines: list, default_pts: int = 0) -> list:
    """Extrait les questions depuis une liste de lignes de texte."""
    questions = []
    cur = None

    def flush():
        if cur:
            questions.append(dict(cur))

    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        # Ignorer les numeros seuls (ex: "2" sur une ligne = fragment d'OCR)
        if _STANDALONE_NUM.match(raw):
            if cur:
                cur['text'] += ' [2]'   # agregation approximative
            continue

        matched = False

        # Bullet Symbol-font ou Unicode
        m = _Q_BULLET.match(raw)
        if m:
            flush(); cur = {'text': _fix_ocr(m.group(1)), 'points': default_pts}
            matched = True

        # Tiret -
        if not matched:
            m = _Q_DASH.match(raw)
            if m:
                flush(); cur = {'text': _fix_ocr(m.group(1)), 'points': default_pts}
                matched = True

        # Lettre a) b)
        if not matched:
            m = _Q_LETTER.match(raw)
            if m:
                flush(); cur = {'num': m.group(1), 'text': _fix_ocr(m.group(2)), 'points': default_pts}
                matched = True

        # Numero 1. 1- 1)
        if not matched:
            m = _Q_NUM.match(raw)
            if m:
                num = int(m.group(1)); txt = m.group(2).strip()
                if num <= 25 and len(txt) > 3 and not txt.isupper() and not re.match(r'^\d{4}', txt):
                    flush(); cur = {'num': num, 'text': _fix_ocr(txt), 'points': default_pts}
                    matched = True

        # Continuation multi-ligne
        if not matched and cur:
            if s and len(s) > 2 and not s.isupper():
                n = _norm(raw)
                if not _THEME_N.match(n) and not _PARTIE_N.match(n):
                    cur['text'] += ' ' + _fix_ocr(s)

    flush()
    return questions


def _parse_section(content: str, label: str) -> dict:
    """Parse un bloc de section en themes + questions."""
    sec_pts = _pts_tot(label)
    lines   = content.split('\n')
    norms   = [_norm(ln) for ln in lines]

    themes      = []
    cur_theme   = None
    ctx_lines   = []
    q_lines     = []
    default_pts = 0
    in_q        = False
    in_ctx      = False

    def flush():
        nonlocal cur_theme, ctx_lines, q_lines, in_q, in_ctx
        t = cur_theme or {'label': '', 'title': 'Questions'}
        t['context']   = _fix_ocr(' '.join(ctx_lines))
        t['questions'] = _parse_questions(q_lines, default_pts)
        if t['questions'] or t['context']:
            themes.append(t)
        cur_theme = None; ctx_lines = []; q_lines = []; in_q = False; in_ctx = False

    for idx, raw in enumerate(lines):
        s = raw.strip()
        n = norms[idx]
        if not s:
            continue

        # ── Nouveau theme ─────────────────────────────────────────────────────
        m = _THEME_N.match(n)
        if m:
            if cur_theme is not None or ctx_lines or q_lines:
                flush()
            tnum   = (m.group(1) or '').strip().upper()
            ttitle = _drop_pts_from(_original_title(raw))
            pts    = _pts_q(s); ptot = _pts_tot(s)
            if pts: default_pts = pts
            cur_theme = {
                'label'              : f'Theme {tnum}' if tnum else 'Theme',
                'title'              : ttitle,
                'points'             : ptot,
                'points_per_question': pts or default_pts,
            }
            in_q = in_ctx = False
            continue

        # ── Sous-section de type "I- Titre" ───────────────────────────────────
        m = _SUBSEC_N.match(n)
        if m and not in_q and not q_lines:
            if cur_theme is not None or ctx_lines or q_lines:
                flush()
            cur_theme = {'label': s, 'title': '', 'points': _pts_tot(s), 'points_per_question': default_pts}
            in_q = in_ctx = False
            continue

        # ── Marqueur QUESTIONS : ─────────────────────────────────────────────
        if _Q_INTRO_N.match(n):
            in_q = True; in_ctx = False
            pts = _pts_q(s)
            if pts:
                default_pts = pts
                if cur_theme: cur_theme['points_per_question'] = pts
            continue

        # ── Contexte TEXTE : ─────────────────────────────────────────────────
        if _CTX_N.match(n):
            in_ctx = True; in_q = False
            colon = raw.find(':')
            after = raw[colon + 1:].strip() if colon >= 0 else s
            if after: ctx_lines.append(after)
            continue

        # ── Mise a jour des points par question ───────────────────────────────
        pts = _pts_q(s)
        if pts:
            default_pts = pts
            if cur_theme: cur_theme['points_per_question'] = pts

        # ── Question ou contexte ?  ───────────────────────────────────────────
        is_q = bool(_Q_BULLET.match(raw) or _Q_DASH.match(raw) or _Q_LETTER.match(raw)
                    or (_Q_NUM.match(raw) and not s.isupper()))

        if is_q or in_q:
            in_q = True; in_ctx = False
            q_lines.append(raw)
        elif in_ctx or (not in_q and s and not s.isupper() and len(s) > 3):
            ctx_lines.append(s)

    if cur_theme is not None or ctx_lines or q_lines:
        flush()

    return {'label': re.sub(r'\s+', ' ', label.strip()), 'points': sec_pts, 'themes': themes}


def _structure_exam(exam: dict) -> dict:
    """Transforme un examen brut en examen structure avec matieres/sections/questions."""
    text = exam.get('text', '')
    if not text.strip():
        return {**exam, 'structured': False, 'total_questions': 0, 'parts': []}

    body = _strip_header(text)
    mats = _split_matieres(body)

    parts = []
    for mat_name, mat_text in mats:
        m = _PTS_MAT.search(mat_text[:200])
        pts_total = int(m.group(1)) if m else 0

        parties  = _split_parties(mat_text)
        sections = []
        for lbl, content in parties:
            sec = _parse_section(content, lbl)
            if sec['themes']:
                sections.append(sec)

        if sections:
            parts.append({'matiere': mat_name, 'points_total': pts_total, 'sections': sections})

    total_q = sum(
        len(th.get('questions', []))
        for p in parts
        for s in p.get('sections', [])
        for th in s.get('themes', [])
    )

    return {**exam, 'parts': parts, 'structured': bool(parts), 'total_questions': total_q}


# ═══════════════════════════════════════════════════════════════════════════════
class Command(BaseCommand):
    help = 'Structure les examens en sections/themes/questions (regex, sans IA)'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str)
        parser.add_argument('--all',     action='store_true')
        parser.add_argument('--redo',    action='store_true')
        parser.add_argument('--limit',   type=int, default=0)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        subjects = SUBJECTS if options['all'] else ([options['subject']] if options['subject'] else [])
        if not subjects:
            self.stdout.write('Utilise --subject NOM ou --all'); return

        total_ok = total_skip = total_fail = total_q = 0

        for subject in subjects:
            path = os.path.join(JSON_DIR, f'exams_{subject}.json')
            if not os.path.exists(path):
                self.stdout.write(f'[ABSENT] {path}'); continue

            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            exams = data.get('exams', []); modified = False; sok = sq = 0
            self.stdout.write(f'[{subject.upper()}] {len(exams)} examens...')

            for i, exam in enumerate(exams):
                if exam.get('structured') and not options['redo']:
                    total_skip += 1; continue
                if options['limit'] and sok >= options['limit']:
                    break
                try:
                    result  = _structure_exam(exam)
                    q_count = result.get('total_questions', 0)
                    if options['dry_run']:
                        summary = ' | '.join(
                            f"{p['matiere']}:{sum(len(t.get('questions',[])) for s in p['sections'] for t in s.get('themes',[]))}q"
                            for p in result.get('parts', [])
                        )
                        self.stdout.write(f'  [{i+1}] {exam.get("file","?")} -> {summary or "VIDE"}')
                    else:
                        exams[i] = result; modified = True
                    total_q += q_count; sq += q_count
                    total_ok += 1; sok += 1
                except Exception as e:
                    self.stdout.write(f'  ERREUR [{i+1}] {exam.get("file","?")}: {e}')
                    total_fail += 1

            self.stdout.write(f'  -> {sok} structures | {sq} questions')
            if modified and not options['dry_run']:
                data['exams'] = exams
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

        flag = ' [DRY-RUN]' if options['dry_run'] else ''
        self.stdout.write(
            f'\nRESULTAT{flag}: {total_ok} ok | {total_skip} deja OK | '
            f'{total_fail} erreurs | {total_q} questions extraites'
        )
