"""Merge solved MCQ JSON into quiz_*_9e.json (NS4 schema)."""
from __future__ import annotations

import json
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "database"

OUT = {
    "maths": ("quiz_math_9e.json", "array", "M9E"),
    "anglais": ("quiz_anglais_9e.json", "array", "AN9E"),
    "espagnol": ("quiz_espagnol_9e.json", "array", "E9E"),
    "francais": ("quiz_kreyol_9e.json", "object", "K9E"),
    "histoire": ("quiz_sc_social_9e.json", "object", "H9E"),
    "svt": ("quiz_SVT_9e.json", "object", "S9E"),
    "informatique": ("quiz_informatique_9e.json", "object", "I9E"),
    "art": ("quiz_art_9e.json", "object", "A9E"),
}

LABELS = {
    "maths": "Mathématiques", "anglais": "Anglais", "espagnol": "Espagnol",
    "francais": "Kreyòl", "histoire": "Sciences sociales", "svt": "Sciences exp.",
    "informatique": "Technologie", "art": "Art",
}


def load(path: Path, kind: str) -> list[dict]:
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    return d if kind == "array" else d.get("quiz", [])


def save(path: Path, kind: str, label: str, qs: list[dict], prefix: str) -> None:
    for i, q in enumerate(qs, 1):
        q["id"] = f"{prefix}{i}"
    if kind == "array":
        path.write_text(json.dumps(qs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(
            json.dumps({
                "matiere": path.stem.replace("quiz_", "").replace("_9e", ""),
                "level": "9e AF",
                "total": len(qs),
                "description": f"Quiz {label} — QCM examens officiels 9e AF",
                "quiz": qs,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def valid(q: dict, subj: str = "") -> bool:
    opts = q.get("options") or []
    min_opts = 2 if subj == "anglais" else 4
    max_opts = 4
    if not (min_opts <= len(opts) <= max_opts):
        return False
    c = str(q.get("correct", "")).upper()[:1]
    if c not in "ABCD":
        return False
    if len((q.get("question") or "").strip()) < 10:
        return False
    if len(str(q.get("explanation", ""))) < 12:
        return False
    q["correct"] = c
    q["options"] = [str(o).strip() for o in opts]
    q["difficulty"] = q.get("difficulty") if q.get("difficulty") in ("facile", "moyen", "difficile") else "moyen"
    q["timer_seconds"] = int(q.get("timer_seconds") or 30)
    q["category"] = str(q.get("category") or "9e AF")[:80]
    return True


def main() -> None:
    solved_path = DB / "_gemini_responses" / "all_solved.json"
    if not solved_path.exists():
        print("Missing", solved_path)
        return
    solved = json.loads(solved_path.read_text(encoding="utf-8"))
    for subj, new_items in solved.items():
        if subj not in OUT:
            continue
        fname, kind, prefix = OUT[subj]
        path = DB / fname
        existing = load(path, kind)
        seen = {(q.get("question") or "")[:80].lower() for q in existing}
        added = 0
        for q in new_items:
            if not valid(q, subj):
                continue
            k = q["question"][:80].lower()
            if k in seen:
                continue
            seen.add(k)
            existing.append(q)
            added += 1
        save(path, kind, LABELS[subj], existing, prefix)
        print(f"{subj}: +{added} -> total {len(existing)}")


if __name__ == "__main__":
    main()
