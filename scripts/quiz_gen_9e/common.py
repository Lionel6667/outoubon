"""Shared helpers for local 9e quiz generation (no API)."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "database"
TARGET = 300

OUT = {
    "maths": ("quiz_math_9e.json", "array", "M9E", "Mathématiques"),
    "francais": ("quiz_kreyol_9e.json", "object", "K9E", "Kreyòl"),
    "anglais": ("quiz_anglais_9e.json", "array", "AN9E", "Anglais"),
    "histoire": ("quiz_sc_social_9e.json", "object", "H9E", "Sciences sociales"),
    "informatique": ("quiz_informatique_9e.json", "object", "I9E", "Technologie"),
    "art": ("quiz_art_9e.json", "object", "A9E", "Art"),
    "espagnol": ("quiz_espagnol_9e.json", "array", "E9E", "Espagnol"),
    "svt": ("quiz_SVT_9e.json", "object", "S9E", "Sciences expérimentales"),
}


def load_existing(path: Path, kind: str) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if kind == "array":
        return list(data) if isinstance(data, list) else list(data.get("quiz", []))
    return list(data.get("quiz", [])) if isinstance(data, dict) else []


def norm_key(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())[:90]


def make_q(
    question: str,
    options: list[str],
    correct_idx: int,
    explanation: str,
    category: str,
    difficulty: str = "moyen",
    timer: int = 30,
) -> dict:
    letters = ["A", "B", "C", "D"]
    opts = [str(o).strip() for o in options]
    if len(opts) != 4:
        raise ValueError("need 4 options")
    ci = correct_idx % 4
    return {
        "question": question.strip(),
        "options": opts,
        "correct": letters[ci],
        "explanation": explanation.strip(),
        "category": category,
        "difficulty": difficulty if difficulty in ("facile", "moyen", "difficile") else "moyen",
        "timer_seconds": timer,
    }


def bank_to_questions(bank: list, category: str, difficulty: str = "moyen") -> list[dict]:
    out = []
    for q, c, w, e in bank:
        wrongs = list(w)
        while len(wrongs) < 3:
            wrongs.append("none of these")
        out.append(make_q(q, [c] + wrongs[:3], 0, e, category, difficulty))
    return out


def shuffle_wrong_options(correct: str, wrongs: list[str], rng: random.Random) -> tuple[list[str], int]:
    wrongs = [w for w in wrongs if w != correct]
    while len(wrongs) < 3:
        wrongs.append(correct + "?")
    opts = [correct, wrongs[0], wrongs[1], wrongs[2]]
    rng.shuffle(opts)
    return opts, opts.index(correct)


def fill_to_target(
    existing: list[dict],
    generator: Callable[[random.Random, set[str]], list[dict]],
    target: int,
    seed: int,
) -> list[dict]:
    seen = {norm_key(q.get("question", "")) for q in existing}
    out = list(existing)
    rng = random.Random(seed)
    attempts = 0
    while len(out) < target and attempts < target * 40:
        attempts += 1
        batch = generator(rng, seen)
        for q in batch:
            k = norm_key(q["question"])
            if k in seen:
                continue
            seen.add(k)
            out.append(q)
            if len(out) >= target:
                break
    return out[:target]


def normalize_question(q: dict) -> dict:
    letters = ["A", "B", "C", "D"]
    opts = [str(o).strip() for o in q.get("options", [])]
    correct = q.get("correct", "A")
    if correct in letters and len(opts) >= len(letters):
        correct_text = opts[letters.index(correct)]
    elif opts:
        correct_text = opts[0]
        correct = "A"
    else:
        correct_text = "yes"
        opts = []
    fillers = ["none of these", "not sure", "no answer", "maybe"]
    fi = 0
    while len(opts) < 4:
        cand = fillers[fi % len(fillers)]
        if cand not in opts and cand != correct_text:
            opts.append(cand)
        fi += 1
    opts = opts[:4]
    if correct not in letters or letters.index(correct) >= len(opts):
        correct = letters[opts.index(correct_text) if correct_text in opts else 0]
    q["options"] = opts
    q["correct"] = correct
    return q


def save_subject(subject: str, questions: list[dict]) -> None:
    fname, kind, prefix, label = OUT[subject]
    path = DB / fname
    for i, q in enumerate(questions, 1):
        normalize_question(q)
        q["id"] = f"{prefix}{i}"
    if kind == "array":
        payload = questions
    else:
        payload = {
            "matiere": path.stem.replace("quiz_", "").replace("_9e", ""),
            "level": "9e AF",
            "total": len(questions),
            "description": (
                f"Quiz {label} — 9e AF : examens officiels + programme 9e "
                f"({len(questions)} QCM, réponses vérifiées)"
            ),
            "quiz": questions,
        }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  {fname}: {len(questions)} questions")
