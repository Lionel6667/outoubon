"""
resource_index.py — Catalogue de toutes les ressources pédagogiques du site.

Fournit :
  - Accès rapide à tous les quiz par matière et catégorie
  - Titres des chapitres par matière (extraits des notes)
  - Sélection ciblée de questions selon les points faibles d'un élève
  - Catalogue compact pour injection dans le prompt du Smart Coach IA
"""

from __future__ import annotations
import json
import random
import re
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DB_DIR   = BASE_DIR / 'database'

# ─── Mapping matière → fichier quiz ──────────────────────────────────────────
QUIZ_FILES: dict[str, str] = {
    'maths':        'quiz_math.json',
    'physique':     'quiz_physique.json',
    'chimie':       'quiz_chimie.json',
    'svt':          'quiz_SVT.json',
    'francais':     'quiz_kreyol.json',
    'philosophie':  'quiz_philosophie.json',
    'anglais':      'quiz_anglais.json',
    'histoire':     'quiz_sc_social.json',
    'economie':     'quiz_economie.json',
    'informatique': 'quiz_informatique.json',
    'art':          'quiz_art.json',
    'espagnol':     'quiz_espagnol.json',
}

# ─── Mapping matière → fichier note (cours) ──────────────────────────────────
NOTE_FILES: dict[str, str] = {
    'maths':        'note_math.json',
    'physique':     'note_physique.json',
    'chimie':       'note_de_Chimie.json',
    'svt':          'note_SVT.json',
    'francais':     'note_kreyol.json',
    'philosophie':  'note_philosophie.json',
    'anglais':      'note_anglais.json',
    'histoire':     'note_histoire.json',
    'economie':     'note_economie.json',
    'informatique': 'note_informatique.json',
    'art':          'note_art.json',
    'espagnol':     'note_espagnol.json',
}

# ─── URLs des cours interactifs par matière ───────────────────────────────────
COURS_URLS: dict[str, str] = {
    'maths':        '/dashboard/cours/math/',
    'physique':     '/dashboard/cours/physique/',
    'chimie':       '/dashboard/cours/chimie/',
    'svt':          '/dashboard/cours/svt/',
    'francais':     '/dashboard/cours/kreyol/',
    'philosophie':  '/dashboard/cours/philosophie/',
    'anglais':      '/dashboard/cours/anglais/',
    'histoire':     '/dashboard/cours/histoire/',
    'economie':     '/dashboard/cours/economie/',
    'informatique': '/dashboard/cours/informatique/',
    'art':          '/dashboard/cours/art/',
    'espagnol':     '/dashboard/cours/espagnol/',
}

SUBJECT_LABELS: dict[str, str] = {
    'maths':        'Mathématiques',
    'physique':     'Physique',
    'chimie':       'Chimie',
    'svt':          'SVT',
    'francais':     'Kreyòl',
    'philosophie':  'Philosophie',
    'anglais':      'Anglais',
    'histoire':     'Sciences Sociales',
    'economie':     'Économie',
    'informatique': 'Informatique',
    'art':          'Art',
    'espagnol':     'Espagnol',
}

# ─── Cache en mémoire (chargé une seule fois par démarrage) ──────────────────
_quiz_cache:       dict[str, list[dict]] = {}
_chapters_cache:   dict[str, list[str]]  = {}
_exam_items_cache: dict[str, list[dict]] = {}

# ─── Mapping matière → fichier exam JSON ─────────────────────────────────────
EXAM_FILES: dict[str, str] = {
    'maths':        'json/exams_maths.json',
    'physique':     'json/exams_physique.json',
    'chimie':       'json/exams_chimie.json',
    'svt':          'json/exams_svt.json',
    'francais':     'json/exams_francais.json',
    'philosophie':  'json/exams_philosophie.json',
    'anglais':      'json/exams_anglais.json',
    'histoire':     'json/exams_histoire.json',
    'economie':     'json/exams_economie.json',
    'informatique': 'json/exams_informatique.json',
    'art':          'json/exams_art.json',
    'espagnol':     'json/exams_espagnol.json',
}


# ─────────────────────────────────────────────────────────────────────────────
# Quiz
# ─────────────────────────────────────────────────────────────────────────────

def _load_quiz_questions(subject: str) -> list[dict]:
    """Charge et retourne toutes les questions quiz d'une matière (avec cache)."""
    if subject in _quiz_cache:
        return _quiz_cache[subject]

    fname = QUIZ_FILES.get(subject)
    if not fname:
        return []

    fpath = DB_DIR / fname
    if not fpath.exists():
        return []

    try:
        with open(fpath, encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, list):
            questions = [q for q in data if isinstance(q, dict) and 'question' in q]
        elif isinstance(data, dict) and 'quiz' in data:
            questions = [q for q in data['quiz'] if isinstance(q, dict) and 'question' in q]
        else:
            questions = []

        _quiz_cache[subject] = questions
        return questions
    except Exception:
        return []


def get_quiz_categories(subject: str) -> list[str]:
    """Retourne toutes les catégories de quiz disponibles pour une matière (ordonnées)."""
    questions = _load_quiz_questions(subject)
    seen = {}
    for q in questions:
        cat = q.get('category', '')
        if cat:
            seen[cat] = seen.get(cat, 0) + 1
    # Sort by frequency (most common first)
    return [c for c, _ in sorted(seen.items(), key=lambda x: -x[1])]


def get_targeted_questions(
    subject:     str,
    topics:      list[str],
    n:           int = 8,
    difficulty:  Optional[str] = None,
    exclude_ids: Optional[set] = None,
) -> list[dict]:
    """
    Sélectionne n questions ciblées sur les topics donnés pour une matière.

    - topics     : termes à chercher dans category, question, explanation
    - difficulty : 'facile' | 'moyen' | 'difficile' | None (tous niveaux)
    - exclude_ids: set d'IDs à exclure
    Returns list of question dicts (id, question, options, correct, explanation, category, difficulty)
    """
    all_questions = _load_quiz_questions(subject)
    if not all_questions:
        return []

    exclude_ids  = exclude_ids or set()
    topics_lower = [t.lower() for t in topics if t]

    scored: list[tuple[int, dict]] = []
    for q in all_questions:
        if q.get('id') in exclude_ids:
            continue
        if difficulty and q.get('difficulty', '').lower() != difficulty.lower():
            continue

        # Relevance scoring
        score = 0
        searchable = ' '.join([
            q.get('category', ''),
            q.get('question', ''),
            q.get('explanation', ''),
        ]).lower()

        for topic in topics_lower:
            if topic in searchable:
                # Extra weight if topic matches the category directly
                score += 3 if topic in q.get('category', '').lower() else 1

        if score > 0 or not topics_lower:
            scored.append((score, q))

    # Sort by relevance (highest first)
    scored.sort(key=lambda x: -x[0])
    selected = [q for _, q in scored[:n]]

    # If not enough targeted questions, pad with random questions from this subject
    if len(selected) < n:
        selected_set = {id(q) for q in selected}
        remaining = [q for q in all_questions
                     if id(q) not in selected_set and q.get('id') not in exclude_ids]
        random.shuffle(remaining)
        selected.extend(remaining[:n - len(selected)])

    return selected[:n]


def get_quiz_count(subject: str) -> int:
    """Retourne le nombre total de questions quiz disponibles pour une matière."""
    return len(_load_quiz_questions(subject))


# ─────────────────────────────────────────────────────────────────────────────
# Cours / chapitres
# ─────────────────────────────────────────────────────────────────────────────

def _extract_chapters_from_raw(raw_text: str) -> list[str]:
    """Extrait les titres de chapitres depuis le raw_text d'une note JSON."""
    # Patterns qui correspondent aux titres de chapitres dans les notes
    patterns = [
        r'CHAPITRE\s+\d+\s*[—–\-]+\s*(.+)',
        r'Chapitre\s+\d+\s*[—–\-]+\s*(.+)',
        r'chapitre\s+\d+\s*[—–\-]+\s*(.+)',
        r'^={3,}\s*(.+?)\s*={3,}$',
        r'^\d+\s+([A-ZÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑ][^\n]{4,60})$',
    ]

    chapters = []
    seen = set()
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        for pattern in patterns:
            m = re.match(pattern, line, re.IGNORECASE)
            if m:
                title = m.group(1).strip().strip('─═').strip()
                # Sanity checks
                if (title
                        and len(title) > 4
                        and len(title) < 120
                        and title not in seen
                        and not title.startswith('#')
                        and not title.lower().startswith('page')):
                    seen.add(title)
                    chapters.append(title)
                break

    return chapters[:25]


def get_subject_chapters(subject: str) -> list[str]:
    """Retourne la liste des titres de chapitres disponibles pour une matière."""
    if subject in _chapters_cache:
        return _chapters_cache[subject]

    fname = NOTE_FILES.get(subject)
    if not fname:
        return []

    fpath = DB_DIR / fname
    if not fpath.exists():
        return []

    try:
        with open(fpath, encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, dict) and 'raw_text' in data:
            chapters = _extract_chapters_from_raw(data['raw_text'])
        elif isinstance(data, list):
            # Format liste de chapitres (ancienne structure)
            chapters = []
            for item in data:
                if isinstance(item, dict):
                    t = item.get('title') or item.get('chapitre') or item.get('nom', '')
                    if t:
                        chapters.append(str(t))
        else:
            chapters = []

        _chapters_cache[subject] = chapters
        return chapters
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Catalogue global pour le prompt IA
# ─────────────────────────────────────────────────────────────────────────────

def get_full_resource_catalog() -> str:
    """
    Construit un catalogue compact de toutes les ressources pédagogiques.
    Injecté dans le prompt du Smart Coach IA pour qu'il puisse choisir
    des quiz et chapitres précis depuis les vraies ressources disponibles.
    """
    lines = ["=== RESSOURCES DISPONIBLES SUR LE SITE (utilise UNIQUEMENT ces données) ==="]

    for subject, label in SUBJECT_LABELS.items():
        cats     = get_quiz_categories(subject)
        chapters = get_subject_chapters(subject)
        q_count  = get_quiz_count(subject)

        if not cats and not chapters:
            continue

        lines.append(f"\n[{subject}] {label} :")
        if cats:
            lines.append(f"  QUIZ ({q_count} questions) — catégories : {', '.join(cats[:12])}")
        else:
            lines.append(f"  QUIZ : aucune question disponible")
        if chapters:
            lines.append(f"  COURS — chapitres : {', '.join(chapters[:10])}")
        else:
            lines.append(f"  COURS : pas de notes disponibles")

    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Examens réels (exams_*.json) — exercices BAC
# ─────────────────────────────────────────────────────────────────────────────

def _load_exam_items(subject: str) -> list[dict]:
    """
    Charge tous les items structurés des examens BAC pour une matière.
    Chaque item contient : theme, intro, questions, exam_name, year.
    Résultat mis en cache après le premier chargement.
    """
    if subject in _exam_items_cache:
        return _exam_items_cache[subject]

    fname = EXAM_FILES.get(subject)
    if not fname:
        _exam_items_cache[subject] = []
        return []

    fpath = DB_DIR / fname
    if not fpath.exists():
        _exam_items_cache[subject] = []
        return []

    try:
        with open(fpath, encoding='utf-8') as f:
            data = json.load(f)

        items = []
        for exam in data.get('exams', []):
            exam_file = exam.get('file', '')
            year      = exam.get('year', '')
            raw_text  = (exam.get('text') or '').strip()
            # Nom de l'examen sans ".pdf"
            exam_name = exam_file.removesuffix('.pdf') if exam_file else ''

            for item in (exam.get('items') or []):
                theme = (item.get('theme') or '').strip()
                intro = (item.get('intro') or '').strip()
                qs    = item.get('questions') or []
                if theme and (intro or qs):
                    items.append({
                        'theme':      theme,
                        'intro':      intro,
                        'questions':  qs,
                        'difficulte': item.get('difficulte', ''),
                        'exam_name':  exam_name,
                        'year':       year,
                        'raw_text':   raw_text,  # texte OCR original complet de l'examen
                    })

        _exam_items_cache[subject] = items
        return items
    except Exception:
        _exam_items_cache[subject] = []
        return []


def get_exam_exercise_for_topic(
    subject: str,
    topic:   str,
    n:       int = 1,
) -> list[dict]:
    """
    Retourne les n exercices réels d'examens BAC les plus pertinents pour un topic donné.
    Matching par similarité entre le topic et le champ `theme` de chaque item.

    Retourne une liste de dicts : {theme, intro, questions, exam_name, year, difficulte}
    """
    items = _load_exam_items(subject)
    if not items:
        return []

    topic_lower = topic.lower()
    topic_words = {w for w in re.split(r'\W+', topic_lower) if len(w) > 2}

    scored: list[tuple[int, dict]] = []
    for item in items:
        theme_lower = item['theme'].lower()
        theme_words = {w for w in re.split(r'\W+', theme_lower) if len(w) > 2}

        common = topic_words & theme_words
        score  = len(common) * 2
        if topic_lower in theme_lower or theme_lower in topic_lower:
            score += 5
        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:n]]


def get_all_exam_themes(subject: str) -> list[str]:
    """Retourne tous les thèmes uniques disponibles dans les examens d'une matière."""
    items = _load_exam_items(subject)
    seen: set[str] = set()
    themes: list[str] = []
    for item in items:
        t = item['theme']
        if t and t not in seen:
            seen.add(t)
            themes.append(t)
    return themes
