"""
Préparation des exercices locaux pour le chat Astra.
- Blocs type `exercise` : souvent OK mais Question+Solution dans le même texte.
- `examples` / `detailed_examples` : dumps compressés → reformulation IA légère.
"""
from __future__ import annotations

import re

_SOLUTION_SPLIT = re.compile(
    r'\n\s*(solution|reponse|réponse|corrige|corrigé|correction)\s*:\s*',
    re.IGNORECASE,
)


def split_exercise_qa(text: str) -> tuple[str, str]:
    raw = (text or '').strip()
    if not raw:
        return '', ''
    m = _SOLUTION_SPLIT.search(raw)
    if m:
        return raw[:m.start()].strip(), raw[m.end():].strip()
    return raw, ''


def exercise_needs_ai_reformat(block: dict) -> bool:
    """True si l'exercice brut est illisible ou nécessite reformulation IA."""
    btype = (block.get('type') or '').strip().lower()
    if btype != 'exercise':
        return True

    content = (block.get('content') or '').strip()
    if len(content) < 20:
        return True

    question, _ = split_exercise_qa(content)
    q = re.sub(r'^question\s*:\s*', '', question, flags=re.I).strip()
    q = re.sub(r'^exercice\s+\d+\s*:\s*', '', q, flags=re.I).strip()

    if len(q) < 12:
        return True
    if len(q) > 2000:
        return True
    # plusieurs mini-exos sur une ligne (énoncé seul)
    if q.count('f(x)') > 2 or q.lower().count('exemple') > 1:
        return True
    if re.search(r'(?<!\\)lim_\{', q):
        return True
    if '📌' in q or 'À RETENIR' in q:
        return True
    if len(content) > 500 and content[-1] not in '.!?…)]»"':
        if not content.endswith('...') and not content.endswith('…'):
            return True
    return False


def _polish_exercise_text(text: str) -> str:
    from core.exercise_display import _dedupe_obvious_repeats, _wrap_inline_math
    from core.gemini import _global_format_tables

    t = (text or '').strip()
    t = _dedupe_obvious_repeats(t)
    t = _global_format_tables(t)
    t = _wrap_inline_math(t)
    return t


def format_exercise_question_for_chat(block: dict, subject_label: str) -> str | None:
    """
    Retourne l'énoncé seul (sans solution) si le bloc est assez propre.
    Sinon None → appeler la reformulation IA légère.
    """
    if exercise_needs_ai_reformat(block):
        return None

    content = (block.get('content') or '').strip()
    question, _ = split_exercise_qa(content)
    q = re.sub(r'^question\s*:\s*', '', question, flags=re.I).strip()
    q = re.sub(r'^exercice\s+\d+\s*:\s*', '', q, flags=re.I).strip()
    q = _polish_exercise_text(q)

    chapter = (block.get('chapter') or '').strip()
    subchapter = (block.get('subchapter') or '').strip()
    head = f"**{chapter}**"
    if subchapter and subchapter.lower() != chapter.lower() and subchapter.lower() != 'exercice 1':
        head += f" · {subchapter}"

    return (
        f"### Exercice — {subject_label}\n"
        f"{head}\n\n"
        f"{q}\n\n"
        "_À toi ! Essaie avant de demander la correction._"
    )


def build_exercise_reformat_context(block: dict, max_chars: int = 1_400) -> str:
    """Contexte minimal pour reformulation IA (énoncé seul, sans solution)."""
    chapter = block.get('chapter', '')
    subchapter = block.get('subchapter', '')
    body = (block.get('content') or '').strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + '…'
    return (
        "MODE REFORMULATION EXERCICE :\n"
        "- Propose UN seul exercice type BAC, clair et complet.\n"
        "- ÉNONCÉ SEULEMENT : ne donne PAS la solution ni la correction.\n"
        "- Utilise KaTeX ($...$) pour les formules.\n"
        "- Phrases courtes, données numériques explicites.\n\n"
        f"Chapitre : {chapter}\n"
        f"Sous-chapitre : {subchapter}\n"
        f"Contenu source (peut être brut ou inclure la solution — ne la révéle pas) :\n{body}"
    )


def _split_subquestions(enonce: str) -> list[str]:
    """Extrait a) b) c)… depuis un énoncé."""
    text = (enonce or '').strip()
    if not text:
        return []
    parts = re.split(r'(?=\b[a-z]\)\s)', text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if re.match(r'^[a-z]\)\s', p):
            out.append(p)
        elif not out:
            out.append(p)
        else:
            out[-1] = out[-1] + ' ' + p
    return [q for q in out if len(q) > 8]


def block_to_exercise_payload(block: dict, subject: str) -> dict | None:
    """
    Convertit un bloc note_*_ai.json vers le format api_get_exercise / session tuteur.
    Énoncé public ; solution stockée séparément (non affichée au load).
    """
    if not block or exercise_needs_ai_reformat(block):
        return None

    content = (block.get('content') or '').strip()
    question, solution = split_exercise_qa(content)
    q = re.sub(r'^question\s*:\s*', '', question, flags=re.I).strip()
    q = re.sub(r'^exercice\s+\d+\s*:\s*', '', q, flags=re.I).strip()
    q = _polish_exercise_text(q)
    sol = _polish_exercise_text(solution) if solution else ''

    questions = _split_subquestions(q)
    intro = q
    chapter_title = (block.get('chapter') or '').strip()

    return {
        'intro': intro,
        'enonce': intro,
        'questions': questions if len(questions) >= 2 else [],
        'theme': chapter_title or subject.upper(),
        'chapter': chapter_title,
        'matiere': subject.upper(),
        'difficulte': 'moyen',
        'source': 'Programme BAC (notes officielles)',
        'solution': sol,
        'conseils': '',
        '_is_real_bac': False,
        '_from_note_ai': True,
        '_note_ai_id': block.get('id', ''),
    }


def exercise_public_payload(payload: dict) -> dict:
    """Retire solution / réponses du payload envoyé au navigateur."""
    pub = dict(payload or {})
    pub.pop('solution', None)
    if pub.get('reponses'):
        pub['reponses'] = {}
    return pub


def pick_note_ai_exercise(subject: str, chapter: str = '') -> dict | None:
    """Tire un bloc type exercise depuis note_*_ai.json (filtré par chapitre si possible)."""
    import json
    import random
    from pathlib import Path

    from django.conf import settings

    _MAP = {
        'maths': 'note_math_ai.json',
        'physique': 'note_physique_ai.json',
        'chimie': 'note_de_Chimie_ai.json',
        'svt': 'note_SVT_ai.json',
        'economie': 'note_economie_ai.json',
        'philosophie': 'note_philosophie_ai.json',
        'francais': 'note_kreyol_ai.json',
        'art': 'note_art_ai.json',
        'histoire': 'note_sc_social_ai.json',
    }
    file_name = _MAP.get(subject)
    if not file_name:
        return None

    ai_path = Path(settings.BASE_DIR) / 'database' / file_name
    if not ai_path.exists():
        return None

    try:
        data = json.loads(ai_path.read_text(encoding='utf-8-sig', errors='replace'))
        blocks = data.get('blocks', [])
    except Exception:
        return None

    exos = [b for b in blocks if (b.get('type') or '').lower() == 'exercise']
    if not exos:
        return None

    if chapter:
        ch = chapter.strip().lower()
        ch_clean = re.sub(r'^(chapitre|ch)\s*\d+\s*[:\-]\s*', '', ch, flags=re.I).strip()
        filtered = [
            b for b in exos
            if ch_clean and ch_clean in (b.get('chapter') or '').lower()
            or ch in (b.get('chapter') or '').lower()
        ]
        if filtered:
            exos = filtered

    clean = [b for b in exos if not exercise_needs_ai_reformat(b)]
    pool = clean if clean else exos
    return random.choice(pool)


def apply_exercise_display_polish(payload: dict, subject: str) -> dict:
    """Tables + LaTeX inline sur intro/questions."""
    from core.exercise_display import format_exercise_display_local

    if not payload:
        return payload
    try:
        fmt = format_exercise_display_local(
            subject,
            payload.get('intro') or '',
            payload.get('questions') or [],
        )
        payload['intro'] = fmt['intro']
        payload['enonce'] = fmt['intro']
        payload['questions'] = fmt['questions']
    except Exception:
        pass
    return payload
