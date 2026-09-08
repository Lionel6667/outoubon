"""
Exercices anglais / espagnol depuis les notes pédagogiques et les examens structurés.
Aucun OCR brut : uniquement des énoncés déjà reconstruits.
"""
from __future__ import annotations

import json
import random
import re
from functools import lru_cache
from pathlib import Path

from .exo_loader import _extract_sub_questions, normalize_chapter_key

_DB_DIR = Path(__file__).resolve().parent.parent / 'database'

_NOTE_FILES = {
    'anglais': 'note_anglais.json',
    'espagnol': 'note_espagnol.json',
}

_EXAM_FILES = {
    'anglais': 'json/exams_anglais.json',
    'espagnol': 'json/exams_espagnol.json',
}

_STOP = {
    'chapitre', 'chapter', 'exercice', 'exercices', 'sujet', 'partie',
    'les', 'des', 'une', 'the', 'and', 'par', 'sur', 'competencia',
    'compétence', 'competence',
}


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _chapter_title(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ''
    return (
        entry.get('titre')
        or entry.get('title')
        or entry.get('chapter_title')
        or entry.get('name')
        or entry.get('chapter')
        or ''
    ).strip()


def _split_blob(blob: str) -> tuple[str, list[str]]:
    blob = (blob or '').strip()
    if not blob:
        return '', []
    intro, questions = _extract_sub_questions(blob)
    if questions:
        return intro or blob, questions
    # Questions numérotées collées dans le même paragraphe
    parts = re.split(r'(?=(?:^|\n)\s*(?:\d{1,2}[\.)]|[a-zA-Z]\))\s+\S)', blob)
    parts = [p.strip() for p in parts if p and p.strip()]
    if len(parts) >= 2 and re.match(r'^(?:\d{1,2}[\.)]|[a-zA-Z]\))', parts[1]):
        return parts[0], parts[1:]
    return blob, []


def _payload(subject: str, theme: str, intro: str, questions: list, source: str, texte: str = '') -> dict:
    intro = (intro or '').strip()
    questions = [str(q).strip() for q in (questions or []) if str(q).strip()]
    if not questions and intro:
        intro, questions = _split_blob(intro)
    if texte and texte.strip() and texte.strip() not in intro:
        intro = (intro + '\n\n' + texte.strip()).strip() if intro else texte.strip()
    if not questions:
        questions = ['Answer every part of this exercise with complete sentences.']
    if not intro:
        intro = theme or subject.upper()
    return {
        'source': source,
        'source_display': source,
        'theme': theme or subject.upper(),
        'chapter': theme or subject.upper(),
        'subject': subject,
        'intro': intro,
        'enonce': intro,
        'texte': (texte or '').strip(),
        'questions': questions,
        'reponses': {},
        'matiere': subject.upper(),
        'difficulte': 'moyen',
        '_is_real_bac': True,
    }


def _from_note_exercise(subject: str, chapter_title: str, item: dict) -> dict | None:
    raw = (item.get('question') or item.get('enonce') or item.get('prompt') or '').strip()
    if len(raw) < 40:
        return None
    intro, questions = _split_blob(raw)
    return _payload(
        subject,
        chapter_title,
        intro,
        questions,
        f'Programme {subject.capitalize()}',
    )


def _from_exam_item(subject: str, exam: dict, item: dict) -> dict | None:
    enonce = (item.get('enonce') or '').strip()
    texte = (item.get('texte') or '').strip()
    theme = (item.get('theme') or exam.get('file') or subject.upper()).strip()
    year = exam.get('year') or ''
    source = item.get('source') or (f'Bac Haïti {year}' if year else 'Bac Haïti')
    if texte and len(texte) >= 80:
        qs = [enonce] if enonce else []
        intro = 'Read the following passage carefully, then answer the questions.' if subject == 'anglais' else (
            'Lee el texto siguiente con atención y responde a las preguntas.'
        )
        return _payload(subject, theme, intro, qs, source, texte=texte)
    if len(enonce) < 20:
        return None
    intro, questions = _split_blob(enonce)
    if not questions:
        questions = [enonce]
        intro = theme
    return _payload(subject, theme, intro, questions, source)


@lru_cache(maxsize=4)
def get_all_exercises(subject: str) -> list[dict]:
    subject = (subject or '').lower().strip()
    out: list[dict] = []
    note_name = _NOTE_FILES.get(subject)
    if note_name:
        raw = _read_json(_DB_DIR / note_name) or {}
        for ch in raw.get('chapters') or raw.get('chapitres') or []:
            title = _chapter_title(ch)
            for item in ch.get('chapter_exercises') or ch.get('exercices') or []:
                exo = _from_note_exercise(subject, title, item)
                if exo:
                    out.append(exo)
    exam_rel = _EXAM_FILES.get(subject)
    if exam_rel:
        raw = _read_json(_DB_DIR / exam_rel) or {}
        for exam in raw.get('exams') or []:
            if exam.get('needs_ocr') and not exam.get('items'):
                continue
            for item in exam.get('items') or []:
                if not isinstance(item, dict):
                    continue
                exo = _from_exam_item(subject, exam, item)
                if exo:
                    out.append(exo)
    return out


def get_chapters(subject: str) -> list[dict]:
    subject = (subject or '').lower().strip()
    note_name = _NOTE_FILES.get(subject)
    titles: list[str] = []
    seen: set[str] = set()
    if note_name:
        raw = _read_json(_DB_DIR / note_name) or {}
        for i, ch in enumerate(raw.get('chapters') or raw.get('chapitres') or []):
            title = _chapter_title(ch)
            key = title.lower()
            if title and key not in seen:
                seen.add(key)
                titles.append(title)
    if not titles:
        json_ch = _DB_DIR / 'json' / f'chapters_{subject}.json'
        raw = _read_json(json_ch) or {}
        for ch in raw.get('chapters') or []:
            title = _chapter_title(ch)
            key = title.lower()
            if title and key not in seen:
                seen.add(key)
                titles.append(title)
    return [{'id': i + 1, 'title': t, 'num': i + 1} for i, t in enumerate(titles)]


def _matches_chapter(exo: dict, chapter: str) -> bool:
    ch_lower = (normalize_chapter_key(chapter) or chapter).lower().strip()
    if not ch_lower or ch_lower in ('aléatoire', 'aleatoire', 'random'):
        return True
    blob = ' '.join([
        str(exo.get('chapter') or ''),
        str(exo.get('theme') or ''),
        str(exo.get('intro') or '')[:400],
    ]).lower()
    if ch_lower in blob or blob.find(ch_lower[:24]) >= 0:
        return True
    kws = [w for w in re.findall(r'\b[a-zA-ZÀ-ÿ]{4,}\b', ch_lower) if w not in _STOP]
    if not kws:
        return False
    return sum(1 for kw in kws if kw in blob) >= max(1, len(kws) // 2)


def get_random_exercise(subject: str, chapter: str = '') -> dict | None:
    pool = get_all_exercises(subject)
    if not pool:
        return None
    if chapter:
        filtered = [e for e in pool if _matches_chapter(e, chapter)]
        if filtered:
            pool = filtered
    return random.choice(pool)
