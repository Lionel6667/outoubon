"""
PDF Loader — Extrait le contenu des examens PDF pour enrichir les réponses IA.
Les PDFs sont dans BacIA_Django/database/ (535 fichiers, 12 matières).

STRATÉGIE PRIORITAIRE (JSON pré-exportés par matière) :
  1. Si database/json/exams_{subject}.json existe → lecture directe, rapide, complète
  2. Sinon fallback sur l'ancien _pdf_index.json
  
  Pour générer les JSON : python manage.py build_subject_json

ANCIEN CACHE (fallback) :
  1. Premier démarrage → parse tous les PDFs → sauvegarde dans _pdf_index.json (~30s)
  2. Démarrages suivants → charge le JSON en < 1 seconde
  3. Si tu ajoutes des PDFs → supprime _pdf_index.json pour forcer le re-indexage
"""
import json
import os
import re
import threading
import time
from pathlib import Path
from django.conf import settings

# ─── Cache en mémoire (chargé une seule fois au démarrage) ───────────────────
_cache: dict[str, str] = {}       # {filename: full_text}
_cache_lock = threading.Lock()
_cache_loaded = False

# ─── Cache JSON par matière (nouveau système) ─────────────────────────────────
_json_exam_cache:    dict[str, dict] = {}   # {subject: loaded json data}
_json_chapter_cache: dict[str, dict] = {}   # {subject: loaded json data}
_json_cache_lock = threading.Lock()

# ─── Cache note_*.json — contenu extrait par (subject, chapter_num) ───────────
_note_content_cache: dict[tuple, str] = {}  # {(subject, chapter_num): extracted_text}
_note_cache_lock = threading.Lock()


def _get_json_dir() -> Path:
    """Retourne le dossier database/json/ où sont les JSON pré-exportés."""
    return Path(getattr(settings, 'COURSE_DB_PATH', '')) / 'json'


def _load_subject_json(subject: str) -> dict:
    """Charge et met en cache le JSON d'examens pour une matière.
    Retourne {} si le fichier n'existe pas (fallback sur ancien système)."""
    if subject in _json_exam_cache:
        return _json_exam_cache[subject]
    with _json_cache_lock:
        if subject in _json_exam_cache:
            return _json_exam_cache[subject]
        json_file = _get_json_dir() / f'exams_{subject}.json'
        if not json_file.exists():
            _json_exam_cache[subject] = {}
            return {}
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _json_exam_cache[subject] = data
            n = data.get('total_files', 0)
            chars = data.get('total_chars', 0)
            print(f'[JSON Loader] exams_{subject}.json chargé : {n} examens, {chars:,} chars')
            return data
        except Exception as e:
            print(f'[JSON Loader] Erreur chargement exams_{subject}.json : {e}')
            _json_exam_cache[subject] = {}
            return {}


def _load_chapter_json(subject: str) -> dict:
    """Charge et met en cache le JSON de programme/chapitre pour une matière."""
    if subject in _json_chapter_cache:
        return _json_chapter_cache[subject]
    with _json_cache_lock:
        if subject in _json_chapter_cache:
            return _json_chapter_cache[subject]
        json_file = _get_json_dir() / f'chapters_{subject}.json'
        if not json_file.exists():
            _json_chapter_cache[subject] = {}
            return {}
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _json_chapter_cache[subject] = data
            print(f'[JSON Loader] chapters_{subject}.json chargé : {data.get("pages", 0)} pages')
            return data
        except Exception as e:
            print(f'[JSON Loader] Erreur chargement chapters_{subject}.json : {e}')
            _json_chapter_cache[subject] = {}
            return {}


def json_exams_available(subject: str) -> bool:
    """Retourne True si le fichier JSON pré-exporté existe pour cette matière."""
    return (_get_json_dir() / f'exams_{subject}.json').exists()


def get_rebuilt_exercise(subject: str, chapter: str = '') -> dict | None:
    """
    Retourne un exercice depuis le champ 'items' reconstruit par l'IA (rebuild_exam_json).
    Priorité : items reconstruits → None si pas encore fait.
    """
    import random as _random
    data = _load_subject_json(subject)
    if not data or not data.get('exams'):
        return None

    # Collect all rebuilt exercise items across all exams
    chapter_words = [w.lower() for w in chapter.split() if len(w) > 3] if chapter else []

    candidates = []
    for exam in data['exams']:
        if not exam.get('rebuilt') or not exam.get('items'):
            continue
        for item in exam['items']:
            if item.get('type') != 'exercice':
                continue
            intro = (item.get('intro') or '').strip()
            questions = [q for q in item.get('questions', []) if str(q).strip()]
            if not intro or len(questions) < 2:
                continue
            # Score by chapter relevance
            score = 0
            if chapter_words:
                searchable = (item.get('theme', '') + ' ' + intro).lower()
                score = sum(1 for w in chapter_words if w in searchable)
            candidates.append((score, item, exam))

    if not candidates:
        return None

    # Sort by score desc then shuffle within same score for variety
    candidates.sort(key=lambda x: (-x[0], _random.random()))
    _, item, exam = candidates[0]

    year = exam.get('year', '?')
    fname = exam.get('file', '')
    intro = item.get('intro', '').strip()
    questions = [str(q).strip() for q in item.get('questions', []) if str(q).strip()]

    return {
        'intro':      intro,
        'enonce':     intro + '\n\n' + '\n'.join(questions),
        'questions':  questions,
        'theme':      item.get('theme', subject.upper()).strip(),
        'matiere':    subject.upper(),
        'difficulte': item.get('difficulte', 'moyen'),
        'source':     item.get('source', f'Bac Haïti {year} — {fname}'),
        'solution':   '',
        'conseils':   f"Exercice extrait d'un vrai examen du Bac Haïti {year} en {subject.upper()}.",
    }


def get_rebuilt_question(subject: str, chapter: str = '', qtype: str = '') -> dict | None:
    """
    Retourne un item question/dissertation/etc. depuis le champ 'items' reconstruit.
    qtype filtre par type ('dissertation', 'question_texte', etc.) — vide = tous types.
    """
    import random as _random
    data = _load_subject_json(subject)
    if not data or not data.get('exams'):
        return None

    chapter_words = [w.lower() for w in chapter.split() if len(w) > 3] if chapter else []
    valid_types = {'question', 'question_texte', 'dissertation', 'qcm', 'production_ecrite'}

    candidates = []
    for exam in data['exams']:
        if not exam.get('rebuilt') or not exam.get('items'):
            continue
        for item in exam['items']:
            itype = item.get('type', 'question')
            if itype not in valid_types:
                continue
            if qtype and itype != qtype:
                continue
            enonce = (item.get('enonce') or '').strip()
            if not enonce:
                continue
            score = 0
            if chapter_words:
                searchable = (item.get('theme', '') + ' ' + enonce[:300]).lower()
                score = sum(1 for w in chapter_words if w in searchable)
            candidates.append((score, item, exam))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (-x[0], _random.random()))
    _, item, exam = candidates[0]

    return {
        'type':       item.get('type', 'question'),
        'enonce':     item.get('enonce', ''),
        'texte':      item.get('texte', ''),
        'options':    item.get('options', []),
        'theme':      item.get('theme', ''),
        'difficulte': item.get('difficulte', 'moyen'),
        'source':     item.get('source', f"Bac Haïti {exam.get('year','?')}"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POOLS D'ITEMS — pour le contrôle qualité IA avant diffusion
# ─────────────────────────────────────────────────────────────────────────────

# Types d'items selon leur destination
_QUIZ_TYPES       = {'question', 'qcm'}
_EXERCISE_TYPES   = {'exercice'}
_EXAM_BLANC_TYPES = {'dissertation', 'question_texte', 'production_ecrite'}


def get_quiz_items_pool(subject: str, chapter: str = '', size: int = 30) -> list:
    """
    Retourne un pool de `size` items de type quiz (question + qcm)
    depuis les JSON reconstruits, triés par pertinence chapitre puis aléatoire.
    Utilisé par le contrôle qualité IA avant de servir les questions.
    """
    import random as _random
    data = _load_subject_json(subject)
    if not data:
        return []

    chapter_words = [w.lower() for w in chapter.split() if len(w) > 2] if chapter else []
    candidates = []

    for exam in data.get('exams', []):
        if not exam.get('rebuilt') or not exam.get('items'):
            continue
        year = exam.get('year', '?')
        for item in exam['items']:
            itype = item.get('type', '')
            if itype not in _QUIZ_TYPES:
                continue
            enonce = (item.get('enonce') or '').strip()
            if len(enonce) < 15:
                continue
            # For QCM: must have options
            if itype == 'qcm' and len(item.get('options', [])) < 2:
                continue
            score = 0
            if chapter_words:
                searchable = (item.get('theme', '') + ' ' + enonce[:400]).lower()
                score = sum(1 for w in chapter_words if w in searchable)
            candidates.append((score, _random.random(), {**item, '_year': year}))

    if not candidates:
        return []

    candidates.sort(key=lambda x: (-x[0], x[1]))
    return [c[2] for c in candidates[:size]]


def get_exercise_items_pool(subject: str, chapter: str = '', chapter_num: int | None = None, size: int = 20) -> list:
    """
    Retourne un pool d'exercices reconstruits triés par pertinence chapitre.
    Utilisé par le contrôle qualité IA avant de servir les exercices.
    
    Args:
        subject: matière (maths, anglais, etc.)
        chapter: titre du chapitre (ex: "Mots Composés & Préfixes")
        chapter_num: numéro du chapitre (1-indexed) - si fourni, utilise une approche aléatoire
        size: nombre d'exercices à retourner
    """
    import random as _random
    data = _load_subject_json(subject)
    if not data:
        return []

    candidates = []
    
    # If chapter_num provided, return random exercises (since pedagogical chapters don't map to exercise items)
    # Otherwise, try to match by chapter name/keywords
    chapter_words = [w.lower() for w in chapter.split() if len(w) > 2] if chapter and not chapter_num else []

    for exam in data.get('exams', []):
        if not exam.get('rebuilt') or not exam.get('items'):
            continue
        year = exam.get('year', '?')
        for item in exam['items']:
            if item.get('type') != 'exercice':
                continue
            intro = (item.get('intro') or '').strip()
            questions = [q for q in item.get('questions', []) if str(q).strip()]
            if not intro or len(questions) < 2:
                continue
            score = 0
            if chapter_words:
                searchable = (item.get('theme', '') + ' ' + intro[:400]).lower()
                score = sum(1 for w in chapter_words if w in searchable)
            candidates.append((score, _random.random(), {**item, '_year': year}))

    if not candidates:
        return []

    candidates.sort(key=lambda x: (-x[0], x[1]))
    return [c[2] for c in candidates[:size]]


def get_exam_blanc_items_pool(subject: str, types: list | None = None, size: int = 30) -> list:
    """
    Retourne un pool d'items pour examen blanc :
    dissertation, question_texte, production_ecrite.
    `types` filtre optionnel : ex ["dissertation", "question_texte"]
    """
    import random as _random
    data = _load_subject_json(subject)
    if not data:
        return []

    allowed = set(types) if types else _EXAM_BLANC_TYPES
    candidates = []

    for exam in data.get('exams', []):
        if not exam.get('rebuilt') or not exam.get('items'):
            continue
        year = exam.get('year', '?')
        for item in exam['items']:
            itype = item.get('type', '')
            if itype not in allowed:
                continue
            enonce = (item.get('enonce') or '').strip()
            if len(enonce) < 20:
                continue
            candidates.append((_random.random(), {**item, '_year': year}))

    _random.shuffle(candidates)
    return [c[1] for c in candidates[:size]]


def get_raw_exam_texts_for_ai(subject: str, chapter: str = '', max_chars: int = 10000) -> str:
    """
    Retourne du texte brut d'examens, utilisé comme MODÈLE de style pour que l'IA
    génère un exercice complet propre.
    - Prend 3-4 examens variés (années différentes)
    - Démarre à PARTIE B pour fournir des modèles d'exercices réels
    - Pondère par pertinence du chapitre
    """
    import random as _random
    data = _load_subject_json(subject)
    if not data or not data.get('exams'):
        return ''

    exams = [e for e in data['exams'] if e.get('text') and len(e.get('text', '')) > 500]
    if not exams:
        return ''

    # Score each exam by chapter keyword match
    chapter_words = [w.lower() for w in chapter.split() if len(w) > 3] if chapter else []
    def _score(exam):
        if not chapter_words:
            return _random.random()  # random order if no chapter
        t = (exam.get('text') or '').lower()
        return sum(1 for w in chapter_words if w in t) + _random.random() * 0.1

    exams_scored = sorted(exams, key=_score, reverse=True)

    # Ensure variety: pick from top-scored but also from different years
    selected = []
    seen_years = set()
    per_exam_limit = max_chars // 3

    # First pass: best matches, one per year
    for exam in exams_scored:
        year = exam.get('year', '?')
        if year in seen_years:
            continue
        raw = exam.get('text', '')
        # Start at PARTIE B to get real exercise models
        parti_b = -1
        for marker in ['PARTIE B.-', 'PARTIE B.', 'PARTIE B', 'Partie B',
                        'PARTIE C', 'Partie C', 'PARTIE II', 'B.-Traiter',
                        'B. Traiter', 'B.Traiter', 'Résoudre']:
            idx = raw.find(marker)
            if idx != -1:
                parti_b = idx
                break
        start = parti_b if parti_b != -1 else max(0, len(raw) // 3)  # skip first third if no marker
        chunk = raw[start:start + per_exam_limit].strip()
        if len(chunk) < 200:
            chunk = raw[:per_exam_limit].strip()
        fname = exam.get('file', '')
        selected.append(f'--- Bac Haïti {year} ({fname}) ---\n{chunk}')
        seen_years.add(year)
        if len(selected) >= 4:
            break

    # Second pass: fill remaining quota from any exam
    if len(selected) < 2:
        for exam in exams_scored[len(selected):]:
            raw = exam.get('text', '')
            chunk = raw[:per_exam_limit].strip()
            year = exam.get('year', '?')
            selected.append(f'--- Bac Haïti {year} ---\n{chunk}')
            if len(selected) >= 3:
                break

    if not selected:
        return ''

    return '\n\n'.join(selected)[:max_chars]


def get_exam_context_json(subject: str, max_chars: int = 4000, variety_seed: int = 0) -> str:
    """
    Retourne du contexte d'examen depuis les JSON pré-exportés.
    Sélectionne plusieurs examens variés (différentes années/séries) pour max de richesse.
    variety_seed permet de varier les examens sélectionnés entre plusieurs appels.
    """
    data = _load_subject_json(subject)
    if not data or not data.get('exams'):
        return ''

    exams = data['exams']
    if not exams:
        return ''

    # Trier par ordre décalé selon variety_seed pour varier les examens
    n = len(exams)
    offset = (variety_seed * 7) % n  # décalage pseudo-aléatoire mais reproductible
    rotated = exams[offset:] + exams[:offset]

    # Prendre des examens de différentes années pour maximiser la diversité
    selected_texts = []
    total_chars = 0
    seen_years = set()

    # Premier passage : 1 examen par année
    for exam in rotated:
        year = exam.get('year', '?')
        if year not in seen_years and total_chars < max_chars * 0.8:
            txt = exam.get('text', '')
            if not txt:
                continue
            chunk = txt[:max_chars // 4]  # max 25% du quota par examen
            selected_texts.append(f'[{exam["file"]} | {year}]\n{chunk}')
            total_chars += len(chunk)
            seen_years.add(year)

    # Deuxième passage si on n'a pas rempli le quota : prendre d'autres examens
    if total_chars < max_chars // 2:
        for exam in rotated:
            if total_chars >= max_chars:
                break
            txt = exam.get('text', '')
            if not txt:
                continue
            chunk = txt[:max_chars // 5]
            selected_texts.append(f'[{exam["file"]}]\n{chunk}')
            total_chars += len(chunk)

    return '\n\n---EXAMEN---\n\n'.join(selected_texts)[:max_chars]


def get_chapter_context_json(subject: str, max_chars: int = 3000) -> str:
    """Retourne le texte du programme/chapitre depuis le JSON pré-exporté."""
    data = _load_chapter_json(subject)
    if not data:
        return ''
    text = data.get('text', '')
    return text[:max_chars] if text else ''


def get_all_exam_texts_json(subject: str) -> list[dict]:
    """
    Retourne la liste de tous les examens d'une matière (pour les quizz, diagnostics, etc.)
    Chaque élément : {'file', 'year', 'series', 'text', 'pages', 'chars'}
    """
    data = _load_subject_json(subject)
    return data.get('exams', []) if data else []


# ─── Nouveaux helpers basés sur les JSON structurés ──────────────────────────

def get_chapters_from_note_json(subject: str) -> list[dict]:
    """
    Retourne la liste des chapitres depuis note_{subject}.json (fichiers de cours).
    Gère tous les formats : JSON chapitres[], JSON sections[], plain-text CHAPITRE/PARTIE/KONPETANS.
    FALLBACK: si note_*.json est vide/corrompu, utilise chapters_*.json.
    """
    # ── Per-subject file + format mapping ─────────────────────────────────────
    _FILE_MAP: dict[str, tuple[str, str]] = {
        'maths':       ('note_math.json',        'json_chapitres'),
        'physique':    ('note_physique.json',    'json_chapitres'),
        'chimie':      ('note_de_Chimie.json',   'json_chapitres'),
        'svt':         ('note_SVT.json',         'json_chapitres'),
        'economie':    ('note_economie.json',    'json_chapitres'),
        'philosophie': ('note_philosophie.json', 'json_chapitres'),
        'francais':    ('note_kreyol.json',      'json_chapitres'),
        'anglais':     ('note_anglais.json',     'json_chapitres'),
        'espagnol':    ('note_espagnol.json',    'json_chapitres'),
        'informatique':('note_informatique.json','json_chapitres'),
        'art':         ('note_art.json',         'json_chapitres'),
        'histoire':    ('note_sc_social.json',   'json_chapitres'),
    }

    cfg = _FILE_MAP.get(subject)
    if not cfg:
        return []

    filename, fmt = cfg
    db_path = Path(__file__).resolve().parent.parent / 'database'
    note_file = db_path / filename
    chapters: list[dict] = []

    if note_file.exists():
        try:
            raw_text = note_file.read_text(encoding='utf-8-sig', errors='replace')

            # ── JSON-based formats ─────────────────────────────────────────
            if fmt == 'json_chapitres':
                try:
                    raw_for_json = raw_text.strip()
                    if raw_for_json.startswith('```'):
                        raw_for_json = re.sub(r'^```\w*\s*', '', raw_for_json)
                        raw_for_json = re.sub(r'\s*```\s*$', '', raw_for_json)
                    data = json.loads(raw_for_json)
                    for key in ('chapitres', 'chapters'):
                        lst = data.get(key) if isinstance(data, dict) else None
                        if isinstance(lst, list) and lst:
                            chapters = [
                                {
                                    'id': i + 1,
                                    'title': ch.get('chapter_title', ch.get('titre', ch.get('title', f'Chapitre {i+1}'))),
                                    'num': i + 1,
                                }
                                for i, ch in enumerate(lst)
                                if isinstance(ch, dict) and (ch.get('chapter_title') or ch.get('titre') or ch.get('title'))
                            ]
                            break
                except Exception:
                    # Fallback for malformed JSON note files: extract chapter titles directly.
                    title_matches = re.findall(
                        r'"chapter_title"\s*:\s*"([^"\n]+)"',
                        raw_text,
                        flags=re.IGNORECASE,
                    )
                    if not title_matches:
                        title_matches = re.findall(
                            r'"(?:titre|title)"\s*:\s*"([^"\n]+)"',
                            raw_text,
                            flags=re.IGNORECASE,
                        )
                    chapters = [
                        {'id': i + 1, 'title': t.strip(), 'num': i + 1}
                        for i, t in enumerate(title_matches)
                        if t and t.strip()
                    ]

            elif fmt == 'json_sections':
                try:
                    data = json.loads(raw_text)
                    flat = []
                    for sec in data.get('sections', []):
                        for ch in sec.get('chapters', []):
                            flat.append(ch.get('title', ch.get('titre', '')))
                    chapters = [
                        {'id': i + 1, 'title': t, 'num': i + 1}
                        for i, t in enumerate(flat) if t
                    ]
                except Exception:
                    pass

            # ── Plain-text formats ─────────────────────────────────────────
            else:
                if fmt == 'text_partie':
                    heading_re = re.compile(r'^#\s+PARTIE\s+(\d+)\s*[\-—–]\s*(.*)$')
                elif fmt == 'text_konpetans':
                    heading_re = re.compile(r'^KONPETANS\s+(\d+)\s*[\-—–]\s*(.*)$')
                else:  # text_chapitre (default)
                    heading_re = re.compile(r'^\s*(?:#+\s*)?CHAPITRE\s+(\d+)\s*[\-:—–]?\s*(.*)$', re.IGNORECASE)

                seen: set[str] = set()
                for ln in raw_text.splitlines():
                    m = heading_re.match(ln.strip() if fmt != 'text_partie' else ln)
                    if not m:
                        m = heading_re.search(ln) if fmt == 'text_chapitre' else None
                    if m:
                        title = (m.group(2) or '').strip() or f'Chapitre {m.group(1)}'
                        key = title.lower()
                        if key not in seen:
                            seen.add(key)
                            chapters.append({'id': len(chapters) + 1, 'title': title, 'num': len(chapters) + 1})

        except Exception as e:
            print(f'[PDF Loader] Error loading {filename}: {e}')

    return chapters


def get_chapters_from_json(subject: str) -> list[dict]:
    """
    Retourne la liste des chapitres depuis chapters_{subject}.json.
    Chaque chapitre : {num, title, matiere, competences, contenus, definitions, summary, structured}
    """
    data = _load_chapter_json(subject)
    return data.get('chapters', []) if data else []


# ─── Universal chapter extraction — per-subject config ───────────────────────
_SUBJECT_NOTE_CONFIG: dict[str, tuple[str, str]] = {
    'maths':       ('note_math.json',        'json_chapitres'),
    'physique':    ('note_physique.json',    'json_chapitres'),
    'chimie':      ('note_de_Chimie.json',   'json_chapitres'),
    'svt':         ('note_SVT.json',         'json_chapitres'),
    'economie':    ('note_economie.json',    'json_chapitres'),
    'philosophie': ('note_philosophie.json', 'json_chapitres'),
    'francais':    ('note_kreyol.json',      'json_chapitres'),
    'anglais':     ('note_anglais.json',     'json_chapitres'),
    'espagnol':    ('note_espagnol.json',    'json_chapitres'),
    'informatique':('note_informatique.json','json_chapitres'),
    'art':         ('note_art.json',         'json_chapitres'),
    'histoire':    ('note_sc_social.json',   'json_chapitres'),
}

# Compiled heading patterns for plain-text formats
_NOTE_HEADING_RE: dict[str, re.Pattern] = {
    'text_chapitre':  re.compile(r'CHAPITRE\s+(\d+)\b', re.IGNORECASE),
    'text_partie':    re.compile(r'^#\s+PARTIE\s+(\d+)\s*[\-—–]', re.IGNORECASE),
    'text_konpetans': re.compile(r'^KONPETANS\s+(\d+)\s*[\-—–]', re.IGNORECASE),
}

# Keys to skip when converting JSON chapters to teaching text
_JSON_COURSE_SKIP: frozenset = frozenset({
    'id', 'generated_at', 'annee_creation', 'source', 'auteur', 'version',
    'series_concernees', 'niveaux_cibles', 'structure_examen', 'structure_exam',
    'analyse_des_examens', 'meta', 'qcm', 'quiz', 'quizz',
    'series_concernees', 'frequence_bac',
})


def _extract_text_section(text: str, chapter_num: int, pattern: re.Pattern) -> str:
    """
    Extrait le contenu de la section N depuis un fichier note plain-text.
    Utilise le pattern fourni pour détecter les débuts de section.
    Retourne le contenu complet entre section N et section N+1 (sans troncature).
    """
    lines = text.split('\n')
    section_starts: list[tuple[int, int]] = []  # (num, line_index)
    seen: set[int] = set()

    for i, line in enumerate(lines):
        m = pattern.search(line)
        if m:
            try:
                n = int(m.group(1))
            except (IndexError, ValueError):
                continue
            if n not in seen:
                seen.add(n)
                section_starts.append((n, i))

    if not section_starts:
        return ''

    for idx, (n, li) in enumerate(section_starts):
        if n == chapter_num:
            end = section_starts[idx + 1][1] if idx + 1 < len(section_starts) else len(lines)
            return '\n'.join(lines[li:end]).strip()

    return ''


def _json_chapter_to_text(obj, depth: int = 0) -> str:
    """
    Convertit récursivement un objet chapitre JSON en texte pédagogique lisible.
    Inclut tout le contenu utile pour enseigner ; ignore les métadonnées internes.
    """
    if obj is None or obj == '' or obj == [] or obj == {}:
        return ''
    if isinstance(obj, bool):
        return 'Oui' if obj else 'Non'
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, str):
        return obj.strip()

    if isinstance(obj, list):
        parts = []
        for item in obj:
            t = _json_chapter_to_text(item, depth)
            if t.strip():
                parts.append(f'• {t}' if isinstance(item, str) else t)
        return '\n'.join(p for p in parts if p.strip())

    if isinstance(obj, dict):
        # Detect title key
        title = ''
        for tkey in ('chapter_title', 'subchapter_title', 'titre', 'title', 'nom', 'name'):
            if tkey in obj and isinstance(obj[tkey], str) and obj[tkey].strip():
                title = obj[tkey].strip()
                break
        parts = []
        if title:
            hdr_level = min(depth + 2, 5)
            parts.append('#' * hdr_level + f' {title}')

        for key, val in obj.items():
            if key in _JSON_COURSE_SKIP:
                continue
            if key in ('chapter_title', 'subchapter_title', 'titre', 'title', 'nom', 'name') and title:
                continue
            if val is None or val == '' or val == [] or val == {}:
                continue
            label = key.replace('_', ' ').capitalize()
            rendered = _json_chapter_to_text(val, depth + 1)
            if rendered.strip():
                parts.append(f'**{label}** :\n{rendered}')

        return '\n\n'.join(p for p in parts if p.strip())

    return str(obj)


def get_note_chapter_content(subject: str, chapter_num: int) -> str:
    """
    Retourne le contenu COMPLET d'un chapitre depuis note_*.json.
    Résultat caché en mémoire — le fichier JSON n'est lu qu'une seule fois par (matière, chapitre).
    """
    cache_key = (subject, chapter_num)

    # Fast path — déjà en cache
    if cache_key in _note_content_cache:
        return _note_content_cache[cache_key]

    # Slow path — lecture + extraction (une seule fois)
    with _note_cache_lock:
        if cache_key in _note_content_cache:
            return _note_content_cache[cache_key]
        result = _load_note_chapter_content(subject, chapter_num)
        _note_content_cache[cache_key] = result
        return result


def _load_note_chapter_content(subject: str, chapter_num: int) -> str:
    """Charge réellement le contenu depuis le disque (appelé une seule fois par clé)."""
    cfg = _SUBJECT_NOTE_CONFIG.get(subject)
    if not cfg:
        return get_course_context(subject, max_chars=6000)

    filename, fmt = cfg
    db_path = Path(__file__).resolve().parent.parent / 'database'
    note_file = db_path / filename
    if not note_file.exists():
        return get_course_context(subject, max_chars=6000)

    try:
        raw = note_file.read_text(encoding='utf-8-sig', errors='replace')
    except Exception:
        return get_course_context(subject, max_chars=4000)

    # Keep the whole chapter whenever possible. The tutor prompt now relies on
    # the complete chapter context instead of a heavily truncated excerpt.
    MAX_CHARS = 90000

    # ── Plain-text extraction ─────────────────────────────────────────────────
    if fmt in _NOTE_HEADING_RE:
        content = _extract_text_section(raw, chapter_num, _NOTE_HEADING_RE[fmt])
        if content and len(content) >= 100:
            return content[:MAX_CHARS]
        return get_course_context(subject, max_chars=4000)

    # ── JSON-based extraction ─────────────────────────────────────────────────
    try:
        raw_for_json = raw.strip()
        if raw_for_json.startswith('```'):
            raw_for_json = re.sub(r'^```\w*\s*', '', raw_for_json)
            raw_for_json = re.sub(r'\s*```\s*$', '', raw_for_json)
        data = json.loads(raw_for_json)
    except (json.JSONDecodeError, ValueError):
        if fmt == 'json_chapitres':
            # Fallback for malformed JSON: isolate the requested chapter block by chapter_title markers.
            starts = [m.start() for m in re.finditer(r'"chapter_title"\s*:\s*"', raw, flags=re.IGNORECASE)]
            if starts and 1 <= chapter_num <= len(starts):
                start = starts[chapter_num - 1]
                end = starts[chapter_num] if chapter_num < len(starts) else len(raw)
                block = raw[start:end].strip()
                if block:
                    return block[:MAX_CHARS]
        return get_course_context(subject, max_chars=4000)

    if fmt == 'json_chapitres':
        ch = None
        for key in ('chapitres', 'chapters'):
            lst = data.get(key) if isinstance(data, dict) else None
            if isinstance(lst, list) and lst:
                if 1 <= chapter_num <= len(lst):
                    ch = lst[chapter_num - 1]
                break
        if ch is None:
            return get_course_context(subject, max_chars=4000)
        content = _json_chapter_to_text(ch, depth=0)
        return content[:MAX_CHARS] if content.strip() else get_course_context(subject, max_chars=4000)

    if fmt == 'json_sections':
        flat: list[dict] = []
        for sec in data.get('sections', []):
            for ch in sec.get('chapters', []):
                flat.append(ch)
        if not (1 <= chapter_num <= len(flat)):
            return get_course_context(subject, max_chars=4000)
        ch = flat[chapter_num - 1]
        # Prefer raw_text (full narrative), fall back to recursive serialization
        rt = ch.get('raw_text', '')
        if rt and len(rt.strip()) >= 100:
            return rt.strip()[:MAX_CHARS]
        content = _json_chapter_to_text(ch, depth=0)
        return content[:MAX_CHARS] if content.strip() else get_course_context(subject, max_chars=4000)

    return get_course_context(subject, max_chars=4000)


def get_math_chapter_plan_from_note(chapter_num: int) -> list[str]:
    """
    Returns the ordered subchapter titles from database/note_math.json.
    This gives a deterministic course progression driven by the note file itself.
    """
    db_path = Path(__file__).resolve().parent.parent / 'database'
    note_file = db_path / 'note_math.json'
    if not note_file.exists():
        return []

    try:
        raw = note_file.read_text(encoding='utf-8-sig', errors='replace')
        data = json.loads(raw)
    except Exception:
        return []

    chapters = data.get('chapters') if isinstance(data, dict) else None
    if not isinstance(chapters, list) or not (1 <= chapter_num <= len(chapters)):
        return []

    chapter = chapters[chapter_num - 1]
    if not isinstance(chapter, dict):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for sub in chapter.get('subchapters', []):
        if not isinstance(sub, dict):
            continue
        title = str(sub.get('title') or '').strip()
        if not title:
            continue
        key = re.sub(r'\s+', ' ', title).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(title)

    return out


_MATH_HYBRID_CHUNK_ORDER: tuple[tuple[str, str], ...] = (
    ('introduction', 'Introduction'),
    ('definition', 'Definition'),
    ('explanation', 'Explication'),
    ('step_by_step', 'Methode'),
    ('examples', 'Exemple'),
    ('detailed_examples', 'Exemple avance'),
    ('methods', 'Regles a retenir'),
    ('common_mistakes', 'Pieges a eviter'),
    ('summary', 'Resume'),
)


def _render_hybrid_chunk_value(value) -> str:
    if value is None or value == '' or value == [] or value == {}:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            rendered = _render_hybrid_chunk_value(item)
            if not rendered:
                continue
            if '\n' in rendered:
                lines.append(rendered)
            else:
                lines.append(f'• {rendered}')
        return '\n'.join(lines).strip()
    if isinstance(value, dict):
        return _json_chapter_to_text(value, depth=0).strip()
    return str(value).strip()


def get_math_hybrid_course_payload(chapter_num: int) -> dict:
    """
    Structured course payload for the hybrid interactive flow.
    Returns one chapter with ordered subchapters and ordered display chunks.
    """
    db_path = Path(__file__).resolve().parent.parent / 'database'
    note_file = db_path / 'note_math.json'
    if not note_file.exists():
        return {}

    try:
        raw = note_file.read_text(encoding='utf-8-sig', errors='replace')
        data = json.loads(raw)
    except Exception:
        return {}

    chapters = data.get('chapters') if isinstance(data, dict) else None
    if not isinstance(chapters, list) or not (1 <= chapter_num <= len(chapters)):
        return {}

    chapter = chapters[chapter_num - 1]
    if not isinstance(chapter, dict):
        return {}

    subchapters_out: list[dict] = []
    for sub_index, sub in enumerate(chapter.get('subchapters', [])):
        if not isinstance(sub, dict):
            continue
        sub_title = str(sub.get('title') or '').strip()
        if not sub_title:
            continue

        chunks: list[dict] = []
        context_parts: list[str] = []
        for key, label in _MATH_HYBRID_CHUNK_ORDER:
            rendered = _render_hybrid_chunk_value(sub.get(key))
            if not rendered:
                continue
            chunks.append({
                'id': key,
                'title': label,
                'content': rendered,
            })
            context_parts.append(f'{label}\n{rendered}')

        if not chunks:
            continue

        subchapters_out.append({
            'index': sub_index,
            'title': sub_title,
            'chunks': chunks,
            'lesson_context': '\n\n'.join(context_parts).strip(),
        })

    if not subchapters_out:
        return {}

    return {
        'chapter_title': str(chapter.get('chapter_title') or '').strip(),
        'chapter_introduction': str(chapter.get('chapter_introduction') or '').strip(),
        'chapter_objectives': [str(item).strip() for item in chapter.get('chapter_objectives', []) if str(item).strip()],
        'subchapters': subchapters_out,
    }


def _split_content_into_chunks(content: str, target_chunk_size: int = 650, target_chunks_per_sub: int = 3) -> list[dict]:
    """
    Intelligently splits markdown content into chunks by paragraphs/sections.
    Returns [{title, content}, ...] list.
    """
    if not content or not content.strip():
        return []

    lines = content.split('\n')
    chunks_out = []
    current_chunk_title = 'Contenu'
    current_chunk_content: list[str] = []
    current_size = 0

    for line in lines:
        line_size = len(line)

        # Detect section headings (### or lower) to use as chunk titles
        heading_match = re.match(r'^(#{3,6})\s+(.+)$', line)
        if heading_match and current_chunk_content and current_size > 100:
            # Save previous chunk before starting new one
            chunk_text = '\n'.join(current_chunk_content).strip()
            if chunk_text:
                chunks_out.append({
                    'title': current_chunk_title,
                    'content': chunk_text,
                })
            current_chunk_title = heading_match.group(2).strip()
            current_chunk_content = []
            current_size = 0

        current_chunk_content.append(line)
        current_size += line_size + 1

        # Flush at blank line only when content is large enough — never cut mid-paragraph
        if not line.strip() and current_size >= target_chunk_size and current_chunk_content:
            chunk_text = '\n'.join(current_chunk_content).strip()
            if chunk_text and len(chunk_text) > 80:
                chunks_out.append({
                    'title': current_chunk_title,
                    'content': chunk_text,
                })
                current_chunk_title = 'Contenu (suite)'
                current_chunk_content = []
                current_size = 0

    # Final chunk
    if current_chunk_content:
        chunk_text = '\n'.join(current_chunk_content).strip()
        if chunk_text and len(chunk_text) > 80:
            chunks_out.append({
                'title': current_chunk_title,
                'content': chunk_text,
            })

    return chunks_out if chunks_out else [{
        'title': 'Contenu',
        'content': content[:3000],
    }]


def _split_content_into_subchapters(content: str) -> list[dict]:
    """
    Splits markdown content into subchapters based on ## headings.
    Each subchapter is then split into chunks.
    Returns [{title, chunks: [{title, content}]}, ...] list.
    """
    if not content or not content.strip():
        return []

    subchapters_out = []
    lines = content.split('\n')
    current_sub_title = 'Introduction'
    current_sub_content: list[str] = []

    def _extract_subchapter_title(line: str) -> str:
        raw = (line or '').strip()
        if not raw:
            return ''

        # Markdown headings: #, ##, ### ...
        m = re.match(r'^#{1,6}\s+(.+?)\s*$', raw)
        if m:
            return m.group(1).strip()

        # Explicit section markers
        m = re.match(r'^(?:Sous[- ]?chapitre|Section|Partie)\s*[:\-]\s*(.+?)\s*$', raw, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()

        # Numbered headings: "1. ...", "2) ...", "3 - ..."
        m = re.match(r'^\d{1,2}\s*[\.)\-]\s+(.+?)\s*$', raw)
        if m:
            return m.group(1).strip()

        # Standalone title-like line ending with ':'
        m = re.match(r'^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\s\-(),]{3,90})\s*:\s*$', raw)
        if m:
            title = m.group(1).strip()
            low = title.lower()
            # Skip generic labels that should remain content lines
            if low in {'introduction', 'definition', 'explication', 'résumé', 'resume', 'summary',
                       'examples', 'example', 'detailed examples', 'methods', 'common mistakes'}:
                return ''
            return title

        return ''

    for line in lines:
        sub_title = _extract_subchapter_title(line)
        if sub_title:
            # Save previous subchapter
            if current_sub_content:
                sub_text = '\n'.join(current_sub_content).strip()
                if sub_text and len(sub_text) > 60:
                    chunks = _split_content_into_chunks(sub_text, target_chunk_size=700)
                    if chunks:
                        subchapters_out.append({
                            'title': current_sub_title,
                            'chunks': chunks,
                            'lesson_context': sub_text[:2200],
                        })
            # Start new subchapter
            current_sub_title = sub_title
            current_sub_content = []
            continue

        current_sub_content.append(line)

    # Final subchapter
    if current_sub_content:
        sub_text = '\n'.join(current_sub_content).strip()
        if sub_text and len(sub_text) > 60:
            chunks = _split_content_into_chunks(sub_text, target_chunk_size=700)
            if chunks:
                subchapters_out.append({
                    'title': current_sub_title,
                    'chunks': chunks,
                    'lesson_context': sub_text[:2200],
                })

    return subchapters_out if subchapters_out else [{
        'title': 'Contenu',
        'chunks': _split_content_into_chunks(content[:4000]),
        'lesson_context': content[:2200],
    }]


def get_generic_hybrid_course_payload(content: str, chapter_title: str = '') -> dict:
    """
    Creates a hybrid payload from raw chapter content (non-math subjects).
    Used for all subjects that don't have structured JSON (SVT, physique, chimie, etc.).
    """
    if not content or not content.strip():
        return {}

    # Remove metadata/artifacts
    content = re.sub(r'^\s*\[\w+\s+\d+\]', '', content, flags=re.MULTILINE)
    content = re.sub(r'\n{3,}', '\n\n', content)

    subchapters = _split_content_into_subchapters(content[:90000])  # Cap at 90k

    if not subchapters:
        return {}

    return {
        'chapter_title': chapter_title or 'Chapitre',
        'chapter_introduction': '',
        'chapter_objectives': [],
        'subchapters': subchapters,
    }


def get_chapter_exercises(subject: str, chapter_num: int, chapter_title: str = '',
                          max_exercises: int = 5) -> str:
    """
    Charge des exercices réels depuis exo_*.json pour un chapitre donné.
    Supporte les schémas : {chapitres:[...]}, [{theme,...}], plain-text.
    Retourne une chaîne lisible pour injection dans le prompt IA.
    """
    _EXO_FILES = {
        'maths':       'exo_math.json',
        'chimie':      'exo_chimie.json',
        'svt':         'exo_svt.json',
        'economie':    'exo_economie.json',
        'physique':    'exo_physique.json',
    }
    db_path = Path(__file__).resolve().parent.parent / 'database'
    fname = _EXO_FILES.get(subject)
    if not fname:
        return ''
    fpath = db_path / fname
    if not fpath.exists():
        return ''

    # exo_math.json is plain text (Markdown) — extract chapter section by keyword
    if subject == 'maths':
        try:
            raw = fpath.read_text(encoding='utf-8-sig', errors='replace')
            # Find section for chapter_num or chapter_title
            lines = raw.split('\n')
            chap_pattern = re.compile(r'Chapitre\s+' + str(chapter_num) + r'\b', re.IGNORECASE)
            start = next((i for i, l in enumerate(lines) if chap_pattern.search(l)), None)
            if start is not None:
                # Grab up to 80 lines
                block = '\n'.join(lines[start:start + 80]).strip()
                return block[:3000] if block else ''
        except Exception:
            pass
        return ''

    try:
        raw = fpath.read_text(encoding='utf-8-sig', errors='replace')
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return ''

    exercises: list[dict] = []

    # Schema A: {chapitres: [{titre, exercices:[...]}]}  — économie, chimie
    if isinstance(data, dict) and 'chapitres' in data:
        chapitres = data['chapitres']
        # Find the chapter by index (chapter_num is 1-based) or by title match
        ch = None
        if chapter_num >= 1 and chapter_num <= len(chapitres):
            ch = chapitres[chapter_num - 1]
        elif chapter_title:
            kw = chapter_title.lower().split()[0] if chapter_title else ''
            ch = next((c for c in chapitres if kw and kw in c.get('titre', '').lower()), None)
        if ch:
            raw_exos = ch.get('exercices', ch.get('exercises', []))
            exercises = raw_exos[:max_exercises]

    # Schema B: [{source, theme, enonce, questions, reponses}]  — SVT
    elif isinstance(data, list):
        if chapter_title:
            kw = chapter_title.lower()
            scored = [(sum(1 for w in kw.split() if w in e.get('theme', '').lower()), e)
                      for e in data if isinstance(e, dict)]
            scored.sort(key=lambda x: x[0], reverse=True)
            exercises = [e for sc, e in scored if sc > 0][:max_exercises]
            if not exercises:
                exercises = data[:max_exercises]
        else:
            exercises = data[:max_exercises]

    # Schema C: {exercices: [...]} or {exams: [...]}  — physique / other
    elif isinstance(data, dict):
        for key in ('exercices', 'exercises', 'exams', 'questions'):
            val = data.get(key)
            if isinstance(val, list) and val:
                exercises = val[:max_exercises]
                break

    if not exercises:
        return ''

    parts = ['=== EXERCICES RÉELS (BAC Haïti) ===']
    for i, ex in enumerate(exercises[:max_exercises], 1):
        if not isinstance(ex, dict):
            continue
        enonce = ex.get('enonce', ex.get('texte', ex.get('sujet', '')))
        questions = ex.get('questions', ex.get('reponses', []))
        source = ex.get('source', '')
        if enonce:
            parts.append(f'\nExercice {i}{(" — " + source) if source else ""} :')
            parts.append(str(enonce)[:600])
            if isinstance(questions, list) and questions:
                for q in questions[:3]:
                    if isinstance(q, dict):
                        txt = q.get('question', q.get('texte', str(q)))
                    else:
                        txt = str(q)
                    if txt:
                        parts.append(f'  → {txt[:200]}')
            elif isinstance(questions, str) and questions:
                parts.append(f'  → {questions[:200]}')

    result = '\n'.join(parts)
    return result[:4000] if len(result) > 80 else ''


def get_exam_text_for_section(subject: str, section_title: str, chapter_title: str = '', max_chars: int = 1200) -> str:
    """
    Cherche dans les examens JSON des passages en rapport avec une section/sous-chapitre.
    Retourne une concaténation des extraits les plus pertinents (max max_chars).
    Zéro appel IA — recherche textuelle par score de mots-clés.
    """
    data = _load_subject_json(subject)
    if not data or not data.get('exams'):
        return ''
    # Mots-clés extraits du titre de section + chapitre
    stop = {'avec','dans','pour','les','des','une','qui','que','sur','est','par',
            'son','ses','leur','leurs','aux','plus','tout','cette','sont','entre'}
    keywords = []
    combined = (section_title + ' ' + chapter_title).lower()
    for w in combined.split():
        w = w.strip('.,;:!?()[]').strip()
        if len(w) > 3 and w not in stop:
            keywords.append(w)
    keywords = list(dict.fromkeys(keywords))[:10]
    if not keywords:
        return ''
    results = []
    for exam in data.get('exams', []):
        source = f"[{exam.get('file','')} — {exam.get('year','')}]"
        # Chercher dans le texte brut de l'examen
        txt = exam.get('text', '')
        if txt:
            lines = txt.split('\n')
            for i, line in enumerate(lines):
                ll = line.lower()
                score = sum(1 for kw in keywords if kw in ll)
                if score >= 2 and len(line.strip()) > 45:
                    context = '\n'.join(lines[i:i+4]).strip()[:350]
                    results.append((score, f"{source}\n{context}"))
        # Chercher dans questions structurées
        for part in exam.get('parts', []):
            for sec in part.get('sections', []):
                for theme in sec.get('themes', []):
                    t_title = theme.get('title', '').lower()
                    t_score = sum(1 for kw in keywords if kw in t_title)
                    if t_score >= 1:
                        for q in theme.get('questions', [])[:3]:
                            qtxt = (q.get('text') or '').strip()
                            if qtxt and len(qtxt) > 40:
                                results.append((t_score + 1, f"{source}\n{theme.get('title','')} : {qtxt[:200]}"))
    results.sort(key=lambda x: -x[0])
    parts = [r[1] for r in results[:4]]
    return '\n\n'.join(parts)[:max_chars]




def get_quiz_questions_from_json(subject: str, count: int = 10, seen_ids: set | None = None) -> list[dict]:
    """
    Extrait des questions depuis les JSON d'examens structurés et les formate en QCM.
    Chaque question retournée : {id, enonce, options, reponse_correcte, explication, sujet, source}
    Les options sont [A, B, C, D] — A est la vraie réponse, puis mélangées avec de faux distracteurs
    basés sur d'autres questions du même sujet.
    """
    import random as _random
    data = _load_subject_json(subject)
    if not data or not data.get('exams'):
        return []

    seen_ids = seen_ids or set()
    all_questions = []

    # Collecter toutes les questions structurées de tous les examens
    for exam in data.get('exams', []):
        if not exam.get('structured'):
            continue
        year = exam.get('year', '?')
        for part in exam.get('parts', []):
            matiere = part.get('matiere', subject)
            for sec in part.get('sections', []):
                for theme in sec.get('themes', []):
                    theme_title = theme.get('title', '')
                    for q in theme.get('questions', []):
                        txt = (q.get('text') or '').strip()
                        # Filtrer les fragments trop courts ou incomplets
                        if not txt or len(txt) < 40:
                            continue
                        # Exclure les questions qui finissent sans verbe (fragments)
                        if txt.endswith(('on', 'un', 'une', 'le', 'la', 'les', 'de', 'du', 'et', 'ou')):
                            continue
                        qid = f"{exam.get('file','')}_{q.get('num','')}"
                        if qid in seen_ids:
                            continue
                        all_questions.append({
                            '_id': qid,
                            '_text': txt,
                            '_theme': theme_title,
                            '_matiere': matiere,
                            '_year': year,
                            '_source': exam.get('file', ''),
                        })

    if not all_questions:
        return []

    _random.shuffle(all_questions)
    selected = all_questions[:count]

    # Formatter comme quiz — questions ouvertes (pas de QCM dans les vrais BAC haïtien)
    result = []
    for i, q in enumerate(selected):
        result.append({
            'id': q['_id'],
            'subject': subject,
            'enonce': q['_text'],
            'options': [],          # vide = question ouverte/réflexion
            'reponse_correcte': -1,  # -1 = pas de choix unique
            'explication': '',
            'sujet': q['_theme'] or q['_matiere'],
            'source': q['_source'],
            'year': q['_year'],
            'is_open': True,        # flag pour le template
        })
    return result


def get_exercise_from_json(subject: str, chapter: str = '', exam_index: int = -1) -> dict | None:
    """
    Retourne un exercice complet (thème + questions) depuis les JSON d'examens.
    Si chapter est fourni, cherche un thème dont le titre contient des mots du chapitre.
    Retourne un dict : {enonce, questions, source, year, theme, matiere, difficulte}
    """
    import random as _random
    data = _load_subject_json(subject)
    if not data or not data.get('exams'):
        return None

    exams = [e for e in data.get('exams', []) if e.get('structured') and e.get('parts')]
    if not exams:
        # Fallback: examens non-structurés avec texte brut
        all_exams = [e for e in data.get('exams', []) if e.get('text')]
        if not all_exams:
            return None
        exam = _random.choice(all_exams)
        text = exam.get('text', '')
        excerpt = text[:2000] if text else ''
        return {
            'enonce': excerpt,
            'questions': [],
            'source': exam.get('file', ''),
            'year': exam.get('year', '?'),
            'theme': 'Extrait d\'examen',
            'matiere': subject.upper(),
            'difficulte': 'moyen',
        }

    # ── Build chapter keyword set (words > 3 chars from chapter name) ────────
    chapter_words = []
    if chapter:
        chapter_words = [w.lower() for w in chapter.split() if len(w) > 3]

    # ── Synonym expansion for common physics/chemistry chapter names ──────────
    _SYNONYMS: dict = {
        'accélération': ['accélération', 'vitesse', 'trajectoire', 'cinématique', 'mouvement', 'Newton', 'forces'],
        'mouvement':    ['accélération', 'vitesse', 'trajectoire', 'cinématique', 'mouvement', 'mécanique'],
        'rectiligne':   ['rectiligne', 'trajectoire', 'mouvement', 'vitesse', 'cinématique'],
        'cinématique':  ['accélération', 'vitesse', 'trajectoire', 'cinématique', 'mouvement'],
        'dynamique':    ['Newton', 'force', 'inertie', 'dynamique', 'principe', 'accélération'],
        'énergie':      ['énergie', 'travail', 'puissance', 'cinétique', 'potentiel', 'conservation'],
        'électrostatique': ['condensateur', 'capacité', 'armature', 'champ', 'potentiel', 'charge'],
        'condensateur': ['condensateur', 'capacité', 'armature', 'charge', 'tension', 'électrostatique'],
        'magnétisme':   ['magnétique', 'induction', 'bobine', 'solénoïde', 'flux', 'Laplace', 'Faraday'],
        'induction':    ['induction', 'bobine', 'solénoïde', 'flux', 'Faraday', 'Lenz', 'magnétique'],
        'optique':      ['lentille', 'réfraction', 'réflexion', 'image', 'vergence', 'Descartes'],
        'circuit':      ['résistance', 'courant', 'tension', 'Ohm', 'Kirchhoff', 'dipôle'],
        'radioactivité':['radioactivité', 'désintégration', 'demi-vie', 'noyau', 'fission', 'fusion'],
        'thermochimie': ['enthalpie', 'exothermique', 'endothermique', 'chaleur', 'Hess', 'énergie'],
        'mendel':       ['allèle', 'phénotype', 'génotype', 'dominant', 'récessif', 'croisement'],
        'photosynthèse':['chlorophylle', 'lumière', 'glucose', 'ATP', 'chloroplaste'],
        'probabilité':  ['probabilité', 'binomiale', 'espérance', 'combinatoire', 'dénombrement'],
        'intégrale':    ['intégrale', 'primitive', 'aire', 'intégration'],
        'dérivée':      ['dérivée', 'tangente', 'extremum', 'variation'],
        'complexe':     ['complexe', 'imaginaire', 'module', 'argument', 'forme'],
    }
    # Expand keywords using synonyms
    expanded = set(chapter_words)
    for wd in chapter_words:
        for key, syns in _SYNONYMS.items():
            if key in wd or wd in key:
                expanded.update(s.lower() for s in syns)

    # ── Score each THEME across ALL exams by searching question + intro text ──
    best_themes = []
    for exam in exams:
        for part in exam.get('parts', []):
            for sec in part.get('sections', []):
                sec_label = sec.get('label', '').upper()
                # PREMIÈRE PARTIE = fill-in-the-blank → heavy penalty
                is_fill_blank = 'PREMI' in sec_label or 'PREMIÈRE' in sec_label or 'PREMIERE' in sec_label
                sec_penalty = -5 if is_fill_blank else 0

                for theme in sec.get('themes', []):
                    qs = [q for q in theme.get('questions', []) if (q.get('text') or '').strip()]
                    if not qs:
                        continue
                    # Skip themes that look like fill-in-the-blank (lots of ___ / …… / dots)
                    all_q_text = ' '.join((q.get('text') or '') for q in qs).lower()
                    blank_count = (all_q_text.count('___') + all_q_text.count('____')
                                   + all_q_text.count('……') + all_q_text.count('......')
                                   + all_q_text.count('= …') + all_q_text.count('est …'))
                    if blank_count >= 3:
                        continue  # skip fill-in-blank themes entirely
                    # Skip if too many questions are exam-instruction/header fragments
                    _INSTR = ['le silence', 'durée de', 'n.b :', 'n.b:', 'recopier', 'partie a', 'partie b',
                               'compléter les', 'compléter le tableau', 'sans calculatrice']
                    instr_count = sum(1 for q in qs if any(p in (q.get('text') or '').lower() for p in _INSTR))
                    if instr_count >= 2:
                        continue  # theme is exam instructions, not a real exercise
                    # Skip themes with scrambled 2-column PDF text
                    # A theme is scrambled if >40% of its tokens are 1-2 char non-digit tokens
                    all_tokens = all_q_text.split()
                    if len(all_tokens) >= 20:
                        short_tok = sum(1 for t in all_tokens if len(t) <= 2 and not t.isdigit())
                        if short_tok / len(all_tokens) > 0.40:
                            continue  # garbled column-merge text, skip entirely
                    # Apply section penalty again (fill-blank section label check)
                    score = sec_penalty
                    if expanded:
                        # Search in intro text + all question texts
                        search_text = (theme.get('intro', '') + ' ' + theme.get('title', '') + ' ' + all_q_text).lower()
                        for kw in expanded:
                            if kw.lower() in search_text:
                                score += 1
                    best_themes.append((score, theme, part.get('matiere', subject.upper()), exam))

    if not best_themes:
        return None

    # Trier par score de pertinence (desc) puis aléatoirement à score égal
    best_themes.sort(key=lambda x: (-x[0], _random.random()))
    best_score = best_themes[0][0]

    # If no keyword matched at all AND chapter was specified → let AI fallback handle it
    if chapter and best_score <= 0:
        return None

    _, theme, matiere, exam = best_themes[0]

    questions = [q for q in theme.get('questions', []) if (q.get('text') or '').strip()]
    enonce_parts = [f"**{theme.get('title', 'Exercice')}**\n"]
    if theme.get('intro'):
        enonce_parts.append(theme['intro'])

    # ── Patterns that mark a question as garbage (exam instructions / fill-blank) ──
    _BAD_PATTERNS = [
        'le silence est', 'durée de l\'épreuve', 'durée de lepreuve',
        'n.b :', 'n.b:', 'n.b.', 'le sujet est composé',
        'recopier et compléter', 'recopier et completer',
        'compléter les phrases', 'compléter le tableau',
        'sans calculatrice', 'sans document', 'barème',
        'partie a.-', 'partie a.', 'partie b.-', 'partie b.',
        'partie c.-', 'partie c.', 'traiter 2 des', 'traiter 3 des',
        'traiter deux', 'traiter trois',
        # fill-in-blank answer markers  (= ……  /  est ……  /  on a ……)
        '= ……', '= ...', 'est ……', 'est ...', '= ………', 'on a ……', 'alors ……',
    ]
    import re as _re
    _DOT_BLANK = _re.compile(r'[.…]{4,}')          # 4+ consecutive dots/ellipsis = blank
    # Scrambled column text: 3+ consecutive single-letter tokens (e.g. "d O o n n j t")
    _SCRAMBLED  = _re.compile(r'(?<!\S)[A-Za-z]\s[A-Za-z]\s[A-Za-z](?!\S)')

    def _is_garbage(txt: str) -> bool:
        tl = txt.lower()
        if len(txt.strip()) < 35:
            return True
        if any(p in tl for p in _BAD_PATTERNS):
            return True
        if _DOT_BLANK.search(txt):
            return True
        # ── Detect scrambled/interleaved 2-column PDF text ───────────────────
        # Check 1: regex for "X Y Z" (single spaced letters) — classic column merge
        if _SCRAMBLED.search(txt):
            return True
        # Check 2: high ratio of 1-2 char tokens  →  garbled column text
        tokens = txt.split()
        if len(tokens) >= 12:
            short = sum(1 for t in tokens if len(t) <= 2 and not t.isdigit())
            if short / len(tokens) > 0.38:
                return True
        return False

    q_texts = []
    for q in questions:
        num = q.get('num', '')
        txt = (q.get('text') or '').strip()
        if txt and not _is_garbage(txt):
            q_texts.append(f"{num} {txt}".strip() if num else txt)

    # ── If fewer than 2 clean questions survive, return None → AI fallback ──
    if len(q_texts) < 2:
        return None

    intro_text = '\n'.join(enonce_parts).strip()
    return {
        'intro': intro_text,
        'enonce': intro_text + ('\n\n' + '\n\n'.join(f"{q}" for q in q_texts) if q_texts else ''),
        'questions': q_texts,
        'source': f"Bac Haïti {exam.get('year', '?')} — {exam.get('file', '')}",
        'year': exam.get('year', '?'),
        'theme': theme.get('title', ''),
        'matiere': matiere,
        'difficulte': 'difficile' if len(q_texts) >= 5 else 'moyen' if len(q_texts) >= 3 else 'facile',
        'solution': '',
        'conseils': f"Ce sujet est extrait d'un vrai examen du Bac Haïti ({exam.get('year','?')}). Lis bien l'énoncé avant de répondre.",
    }



# ─── Mots-clés dans le NOM du fichier → c'est un examen ─────────────────────
# 'exam_' couvre nos fichiers téléchargés style exam_maths_..., exam_physique_...
EXAM_NAME_KEYWORDS = ['examen', 'exam_', 'exam-', 'compilation', 'epreuve', 'sujet_bac', 'bac_', '_bac']

def is_exam_file(filename: str) -> bool:
    """Retourne True si le fichier est identifié comme une compilation d'examens."""
    name_lower = filename.lower().replace(' ', '_')
    return any(kw in name_lower for kw in EXAM_NAME_KEYWORDS)



# Les mots-clés dans le nom du fichier déterminent les matières associées
FILE_SUBJECT_MAP = {
    'geologi': ['svt'],
    'biologi': ['svt'],
    'svt': ['svt'],
    'magnetis': ['physique'],
    'optique': ['physique'],
    'mecanique': ['physique'],
    'physique': ['physique'],
    'chimie': ['chimie'],
    'thermo': ['physique', 'chimie'],
    'maths': ['maths'],
    'math': ['maths'],
    'mathemat': ['maths'],
    'francais': ['francais'],
    'creole': ['francais'],
    'kreyol': ['francais'],
    'histoire': ['histoire'],
    'hist_geo': ['histoire'],
    'hist-geo': ['histoire'],
    'sciences_sociales': ['histoire'],
    'philosophie': ['philosophie'],
    'philo': ['philosophie'],
    'anglais': ['anglais'],
    'informatique': ['informatique'],
    'economie': ['economie'],
    'espagnol': ['espagnol'],
    'art': ['art'],
}

# ─── Mots-clés de contenu par matière (pour la recherche dans le texte) ──────
SUBJECT_CONTENT_KEYWORDS = {
    'svt': [
        # Hérédité (priorité maximale — sort 70% du Bac SVT)
        'génétique', 'genetique', 'hérédité', 'heredite', 'héréditaire', 'hereditaire',
        'daltonisme', 'drépanocytose', 'drepanocytose', 'hémophilie', 'hemophilie',
        'allèle', 'allele', 'génotype', 'genotype', 'phénotype', 'phenotype',
        'homozygote', 'hétérozygote', 'heterozygote', 'chromosome', 'caryotype',
        'locus', 'dominance', 'récessif', 'recessif', 'croisement', 'pedigree',
        'lignée pure', 'lignee pure', 'F1', 'F2', 'loi de Mendel',
        'groupe sanguin', 'lignage', 'transmission', 'gène lié', 'gene lie',
        'mitose', 'méiose', 'meiose', 'fécondation', 'fecondation',
        'ADN', 'ARN', 'mutation', 'gène', 'gene',
        # Biologie générale
        'géologie', 'geologie', 'fossile', 'roche', 'tectonique', 'séisme',
        'volcan', 'croûte', 'manteau', 'biodiversité', 'évolution', 'cellule',
        'écosystème', 'photosynthèse', 'protéine', 'organe', 'immunité', 'lymphocyte',
    ],
    'physique': [
        'magnétisme', 'magnétique', 'champ', 'force', 'newton', 'énergie',
        'travail', 'puissance', 'onde', 'lumière', 'optique', 'circuit',
        'tension', 'courant', 'résistance', 'loi', 'vitesse', 'accélération',
        'mécanique', 'électron', 'proton', 'noyau', 'radioactivité',
    ],
    'chimie': [
        'molécule', 'atome', 'liaison', 'réaction', 'oxydation', 'réduction',
        'acide', 'base', 'pH', 'concentration', 'mole', 'solvant', 'solution',
        'équation', 'enthalpie', 'cinétique', 'titrage', 'alcool', 'ester',
    ],
    'maths': [
        'fonction', 'dérivée', 'intégrale', 'limite', 'suite', 'vecteur',
        'matrice', 'probabilité', 'statistique', 'trigonométrie', 'logarithme',
        'exponentielle', 'complexe', 'théorème', 'démonstration',
    ],
    'francais': [
        'roman', 'poème', 'théâtre', 'métaphore', 'narration', 'personnage',
        'auteur', 'oeuvre', 'littérature', 'genre', 'registre', 'style',
    ],
    'philosophie': [
        'conscience', 'liberté', 'vérité', 'justice', 'bonheur', 'moral',
        'raison', 'nature', 'culture', 'état', 'société', 'politique', 'droit',
    ],
    'histoire': [
        'guerre', 'révolution', 'empire', 'nation', 'colonisation', 'démocratie',
        'totalitarisme', 'résistance', 'indépendance', 'traité', 'géographie',
    ],
}


def _get_db_path() -> Path:
    """Retourne le chemin vers le dossier database des cours PDF."""
    return Path(getattr(settings, 'COURSE_DB_PATH', ''))


def _get_index_cache_path() -> Path:
    """Retourne le chemin du fichier JSON de cache de l'index."""
    default = _get_db_path() / '_pdf_index.json'
    return Path(getattr(settings, 'PDF_INDEX_CACHE', str(default)))


def _load_all_pdfs() -> None:
    """
    Charge et met en cache tous les PDFs/TXTs du dossier database.

    Stratégie cache disque (OPTIMISATION CRITIQUE pour 535 fichiers) :
    - Si _pdf_index.json existe → chargement JSON instantané (< 1s)
    - Sinon → parse tous les PDFs → sauvegarde JSON → ~30-60s une seule fois
    - Pour forcer le re-indexage : supprimer _pdf_index.json
    """
    global _cache_loaded
    db_path = _get_db_path()
    if not db_path or not db_path.exists():
        _cache_loaded = True
        return

    cache_file = _get_index_cache_path()

    # ── Tentative de chargement depuis le cache disque ──────────────────────
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Vérifier que le cache correspond au bon dossier
            if data.get('db_path') == str(db_path) and isinstance(data.get('files'), dict):
                _cache.update(data['files'])
                _cache_loaded = True
                print(f"[PDF Cache] Chargé {len(_cache)} fichiers depuis {cache_file.name} (cache disque)")
                return
        except Exception as e:
            print(f"[PDF Cache] Cache corrompu, re-indexage... ({e})")

    # ── Premier démarrage : parse tous les PDFs et sauvegarde le cache ──────
    print(f"[PDF Cache] Indexage de {db_path} en cours... (une seule fois, ~30-60s)")
    t_start = time.time()

    try:
        import pdfplumber
        has_pdfplumber = True
    except ImportError:
        has_pdfplumber = False
        print("[PDF Cache] pdfplumber non installé — PDFs ignorés, TXTs seulement.")

    all_files = (
        list(db_path.glob('*.pdf')) + list(db_path.glob('**/*.pdf')) +
        list(db_path.glob('*.txt')) + list(db_path.glob('**/*.txt'))
    )
    # Exclure le fichier cache lui-même
    all_files = [f for f in all_files if f.name != '_pdf_index.json']

    seen: set = set()
    parsed = 0
    for file_path in all_files:
        key = file_path.name
        if key in _cache or key in seen:
            continue
        seen.add(key)

        if file_path.suffix.lower() == '.pdf':
            if not has_pdfplumber:
                continue
            try:
                text_pages = []
                with pdfplumber.open(str(file_path)) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text_pages.append(page_text.strip())
                _cache[key] = '\n\n'.join(text_pages)
                parsed += 1
            except Exception:
                _cache[key] = ''

        elif file_path.suffix.lower() == '.txt':
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    _cache[key] = f.read()
                parsed += 1
            except Exception:
                _cache[key] = ''

    elapsed = round(time.time() - t_start, 1)
    print(f"[PDF Cache] {parsed}/{len(all_files)} fichiers indexés en {elapsed}s")

    # ── Sauvegarde du cache sur disque ──────────────────────────────────────
    try:
        cache_data = {
            'db_path': str(db_path),
            'indexed_at': time.strftime('%Y-%m-%d %H:%M'),
            'total': len(_cache),
            'files': dict(_cache),
        }
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=None)
        print(f"[PDF Cache] Cache sauvegardé → {cache_file}")
    except Exception as e:
        print(f"[PDF Cache] Impossible de sauvegarder le cache : {e}")

    _cache_loaded = True


def rebuild_pdf_index() -> int:
    """
    Force le re-indexage complet de tous les PDFs.
    À appeler depuis le management command 'python manage.py rebuild_pdf_index'.
    Retourne le nombre de fichiers indexés.
    """
    global _cache_loaded, _cache
    cache_file = _get_index_cache_path()
    # Supprimer le cache existant pour forcer le re-parsing
    if cache_file.exists():
        cache_file.unlink()
    _cache.clear()
    _cache_loaded = False
    with _cache_lock:
        if not _cache_loaded:
            _load_all_pdfs()
    return len(_cache)


def _ensure_loaded() -> None:
    """Assure que les PDFs sont chargés (thread-safe, une seule fois)."""
    global _cache_loaded
    if not _cache_loaded:
        with _cache_lock:
            if not _cache_loaded:
                _load_all_pdfs()


def _file_matches_subject(filename: str, subject: str) -> bool:
    """Vérifie si un fichier est associé à une matière selon son nom."""
    name_lower = filename.lower()
    for keyword, subjects in FILE_SUBJECT_MAP.items():
        if keyword in name_lower and subject in subjects:
            return True
    return False


def _score_text_for_subject(text: str, subject: str) -> int:
    """Score de pertinence d'un texte pour une matière donnée."""
    keywords = SUBJECT_CONTENT_KEYWORDS.get(subject, [])
    if not keywords:
        return 0
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def _extract_relevant_chunks(text: str, subject: str, max_chars: int = 2500) -> str:
    """Extrait les paragraphes les plus pertinents pour la matière."""
    keywords = SUBJECT_CONTENT_KEYWORDS.get(subject, [])
    if not text:
        return ''

    # Découper en paragraphes
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if len(p.strip()) > 80]

    if not keywords:
        # Retourner le début du document
        return text[:max_chars]

    # Scorer chaque paragraphe
    scored = []
    for para in paragraphs:
        para_lower = para.lower()
        score = sum(1 for kw in keywords if kw.lower() in para_lower)
        if score > 0:
            scored.append((score, para))

    # Trier par pertinence décroissante
    scored.sort(key=lambda x: x[0], reverse=True)

    # Assembler jusqu'à max_chars
    result_parts = []
    total_chars = 0
    for _score, para in scored:
        if total_chars + len(para) > max_chars:
            break
        result_parts.append(para)
        total_chars += len(para)

    if not result_parts:
        # Fallback : retourner le début
        return text[:max_chars]

    return '\n\n'.join(result_parts)


def get_exam_context(subject: str, max_chars: int = 3000, start_idx: int = 0) -> str:
    """
    Retourne UNIQUEMENT le contenu des fichiers d'examens pour une matière.
    Priorité 1 : JSON pré-exportés (database/json/exams_{subject}.json) — riche, complet.
    Priorité 2 : Ancien cache _pdf_index.json (fallback si JSON pas encore généré).
    """
    # ── Essai JSON pré-exporté (nouveau système) ─────────────────────────────
    if json_exams_available(subject):
        return get_exam_context_json(subject, max_chars=max_chars, variety_seed=start_idx)

    # ── Fallback ancien système ───────────────────────────────────────────────
    _ensure_loaded()
    if not _cache:
        return ''

    exam_texts = []
    for filename, text in _cache.items():
        if not text:
            continue
        is_exam = is_exam_file(filename)
        fname_lower = filename.lower()
        subject_in_name = (
            subject in fname_lower or
            any(kw in fname_lower for kw, subjs in FILE_SUBJECT_MAP.items() if subject in subjs)
        )
        if is_exam and subject_in_name:
            score = _score_text_for_subject(text, subject)
            exam_texts.append((max(score, 5), text))

    if not exam_texts:
        return ''

    exam_texts.sort(key=lambda x: x[0], reverse=True)
    slice_end = start_idx + 3
    selected = exam_texts[start_idx:slice_end]
    if not selected:
        selected = exam_texts[:3]
    combined = '\n\n---EXAMEN---\n\n'.join(t for _, t in selected)
    return _extract_relevant_chunks(combined, subject, max_chars)


def get_heredity_context(max_chars: int = 5000) -> str:
    """
    Retourne du texte spécialement sélectionné sur la génétique/hérédité SVT.
    Priorité 1 : JSON pré-exporté (exams_svt.json) — cherche les examens avec mots-clés hérédité.
    Priorité 2 : Ancien cache.
    """
    # ── JSON pré-exporté ─────────────────────────────────────────────────────
    if json_exams_available('svt'):
        exams = get_all_exam_texts_json('svt')
        HEREDITY_KEYS = [
            'génétique', 'génotype', 'phénotype', 'homozygote', 'hétérozygote',
            'daltonisme', 'drépanocytose', 'hémophilie', 'chromosome',
            'croisement', 'allèle', 'pedigree', 'mendel', 'hérédité',
            'groupe sanguin', 'locus', 'transmission', 'lignée pure',
        ]
        scored = []
        for exam in exams:
            text_lower = exam.get('text', '').lower()
            score = sum(1 for kw in HEREDITY_KEYS if kw in text_lower)
            if score > 0:
                scored.append((score, exam.get('text', '')))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            combined = '\n\n---EXAMEN---\n\n'.join(t for _, t in scored[:4])
            return combined[:max_chars]
        # Fallback : prendre tout le contenu SVT
        return get_exam_context_json('svt', max_chars=max_chars)

    # ── Ancien système ────────────────────────────────────────────────────────
    _ensure_loaded()
    if not _cache:
        return ''
    HEREDITY_KEYS = [
        'genetique', 'génétique', 'homozygote', 'heredite', 'dalton',
        'drepanocytose', 'groupe_sanguin', 'hemophilie', 'chromosom',
    ]
    candidate_texts = []
    for filename, text in _cache.items():
        if not text or not is_exam_file(filename):
            continue
        fname_lower = filename.lower()
        is_svt = ('svt' in fname_lower or 'bio' in fname_lower)
        if not is_svt:
            continue
        heredity_score = sum(1 for kw in HEREDITY_KEYS if kw in fname_lower or kw in text.lower()[:3000])
        if heredity_score >= 1:
            candidate_texts.append((heredity_score, text))
    if not candidate_texts:
        return get_exam_context('svt', max_chars=max_chars)
    candidate_texts.sort(key=lambda x: x[0], reverse=True)
    combined = '\n\n---EXAMEN---\n\n'.join(t for _, t in candidate_texts[:3])
    return _extract_relevant_chunks(combined, 'svt', max_chars)


def get_course_context(subject: str, max_chars: int = 3000) -> str:
    """
    Retourne du contenu pertinent pour une matière (cours + examens).
    Priorité 1 : JSON pré-exportés.
    Priorité 2 : Ancien cache.
    """
    if not subject or subject == 'general':
        return ''

    # ── JSON pré-exporté ─────────────────────────────────────────────────────
    if json_exams_available(subject):
        exam_part    = get_exam_context_json(subject, max_chars=max_chars // 2)
        chapter_part = get_chapter_context_json(subject, max_chars=max_chars // 2)
        parts = [p for p in [exam_part, chapter_part] if p]
        return '\n\n'.join(parts) if parts else ''

    # ── Ancien système ────────────────────────────────────────────────────────
    _ensure_loaded()
    if not _cache:
        return ''

    exam_texts = []
    course_texts = []

    for filename, text in _cache.items():
        if not text:
            continue
        if is_exam_file(filename):
            score = _score_text_for_subject(text, subject)
            if score >= 1 or _file_matches_subject(filename, subject):
                exam_texts.append((max(score, 1), text, filename))
        else:
            if _file_matches_subject(filename, subject):
                course_texts.append((999, text, filename))
            else:
                score = _score_text_for_subject(text, subject)
                if score >= 2:
                    course_texts.append((score, text, filename))

    exam_texts.sort(key=lambda x: x[0], reverse=True)
    course_texts.sort(key=lambda x: x[0], reverse=True)

    exam_part  = _extract_relevant_chunks(
        '\n\n'.join(t for _, t, _ in exam_texts[:2]),
        subject, max_chars // 2
    ) if exam_texts else ''
    course_part = _extract_relevant_chunks(
        '\n\n'.join(t for _, t, _ in course_texts[:2]),
        subject, max_chars // 2
    ) if course_texts else ''

    parts = [p for p in [exam_part, course_part] if p]
    return '\n\n'.join(parts)





def get_all_subjects_summary() -> dict[str, str]:
    """Retourne un résumé des cours disponibles par matière (pour le debug)."""
    _ensure_loaded()
    summary = {}
    for filename, text in _cache.items():
        word_count = len(text.split()) if text else 0
        summary[filename] = f"{word_count} mots"
    return summary


def get_loaded_files() -> list[str]:
    """Retourne la liste des fichiers PDF chargés."""
    _ensure_loaded()
    return [f for f, t in _cache.items() if t]
