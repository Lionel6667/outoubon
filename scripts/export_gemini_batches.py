"""Export unsolved MCQ batches for Gemini (browser or API). No DeepSeek."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database"
CLEAN = DB / "_mcq_clean_9e.json"
BATCH_DIR = DB / "_gemini_batches"
BATCH_SIZE = 15

OUT_FILES = {
    "maths": "quiz_math_9e.json",
    "anglais": "quiz_anglais_9e.json",
    "espagnol": "quiz_espagnol_9e.json",
    "francais": "quiz_kreyol_9e.json",
    "histoire": "quiz_sc_social_9e.json",
    "svt": "quiz_SVT_9e.json",
    "informatique": "quiz_informatique_9e.json",
    "art": "quiz_art_9e.json",
}

PROMPT_HEAD = """Tu es professeur Haïti 9e AF ({label}).

Pour chaque QCM OFFICIEL ci-dessous (extrait d'examen, sans corrigé), donne la bonne réponse
UNIQUEMENT si tu es certain à 100%. Sinon OMET la question.

Réponds SEULEMENT un JSON array:
[{{"question":"...","options":["..","..","..",".."],"correct":"B","explanation":"...","category":"...","difficulty":"moyen","timer_seconds":30}}]

QCM:
"""


def load_existing_questions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    qs = data if isinstance(data, list) else data.get("quiz", [])
    return {(q.get("question") or "")[:80].lower() for q in qs}


def main() -> None:
    BATCH_DIR.mkdir(exist_ok=True)
    clean = json.loads(CLEAN.read_text(encoding="utf-8"))
    labels = {
        "maths": "Mathématiques", "anglais": "Anglais", "espagnol": "Espagnol",
        "francais": "Kreyòl", "histoire": "Sciences sociales", "svt": "Sciences exp.",
        "informatique": "Technologie", "art": "Art",
    }
    total_batches = 0
    for subj, items in clean.items():
        seen = load_existing_questions(DB / OUT_FILES.get(subj, ""))
        pending = [q for q in items if q["question"][:80].lower() not in seen]
        if not pending:
            print(f"{subj}: 0 pending")
            continue
        subdir = BATCH_DIR / subj
        subdir.mkdir(exist_ok=True)
        for i in range(0, len(pending), BATCH_SIZE):
            batch = pending[i : i + BATCH_SIZE]
            idx = i // BATCH_SIZE + 1
            body = json.dumps(batch, ensure_ascii=False, indent=2)
            prompt = PROMPT_HEAD.format(label=labels.get(subj, subj)) + body
            (subdir / f"batch_{idx:03d}.txt").write_text(prompt, encoding="utf-8")
            total_batches += 1
        print(f"{subj}: {len(pending)} pending -> {(len(pending)+BATCH_SIZE-1)//BATCH_SIZE} batches")
    print(f"WROTE {total_batches} batch files under {BATCH_DIR}")


if __name__ == "__main__":
    main()
