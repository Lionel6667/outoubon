"""Catalogue des fichiers quiz NS4 + 9e AF."""
from __future__ import annotations

import json
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "database"

QUIZ_FILES_NS4: dict[str, str] = {
    "maths": "quiz_math.json",
    "physique": "quiz_physique.json",
    "chimie": "quiz_chimie.json",
    "svt": "quiz_SVT.json",
    "francais": "quiz_kreyol.json",
    "philosophie": "quiz_philosophie.json",
    "anglais": "quiz_anglais.json",
    "histoire": "quiz_sc_social.json",
    "economie": "quiz_economie.json",
    "informatique": "quiz_informatique.json",
    "art": "quiz_art.json",
    "espagnol": "quiz_espagnol.json",
}

QUIZ_FILES_9E: dict[str, str] = {
    "maths": "quiz_math_9e.json",
    "francais": "quiz_kreyol_9e.json",
    "anglais": "quiz_anglais_9e.json",
    "histoire": "quiz_sc_social_9e.json",
    "informatique": "quiz_informatique_9e.json",
    "art": "quiz_art_9e.json",
    "espagnol": "quiz_espagnol_9e.json",
    "svt": "quiz_SVT_9e.json",
}


def _read_quiz_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get("quiz") or data.get("questions") or []
    else:
        return []
    return [q for q in raw if isinstance(q, dict) and (q.get("question") or q.get("enonce"))]


def load_9e_questions(subject: str) -> list[dict]:
    return _read_quiz_file(DB_DIR / QUIZ_FILES_9E.get(subject, ""))


def load_quiz_questions(subject: str, include_9e: bool = False) -> list[dict]:
    """NS4 + 9e AF (si présents)."""
    out = _read_quiz_file(DB_DIR / QUIZ_FILES_NS4.get(subject, ""))
    if include_9e:
        extra = load_9e_questions(subject)
        out = out + extra
    return out
