"""Build 9e quiz JSON using Google Gemini (NOT DeepSeek).

Reads OCR exam text OR MCQ candidates, asks Gemini to extract+solve QCM
with verified answers only.

Usage:
  set GEMINI_API_KEY=your_key
  python scripts/gemini_9e_quiz_builder.py --subject maths
  python scripts/gemini_9e_quiz_builder.py --all
  python scripts/gemini_9e_quiz_builder.py --from-candidates --subject histoire

NEVER uses DEEPSEEK_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "database" / "json"
DB = ROOT / "database"
CAND = DB / "_mcq_candidates_9e.json"

OUT_FILES = {
    "maths": ("quiz_math_9e.json", "array"),
    "anglais": ("quiz_anglais_9e.json", "array"),
    "espagnol": ("quiz_espagnol_9e.json", "array"),
    "francais": ("quiz_kreyol_9e.json", "object"),
    "histoire": ("quiz_sc_social_9e.json", "object"),
    "svt": ("quiz_SVT_9e.json", "object"),
    "informatique": ("quiz_informatique_9e.json", "object"),
    "art": ("quiz_art_9e.json", "object"),
}

PREFIX = {
    "maths": "M9E", "anglais": "AN9E", "espagnol": "E9E", "francais": "K9E",
    "histoire": "H9E", "svt": "S9E", "informatique": "I9E", "art": "A9E",
}

LABELS = {
    "maths": "Mathématiques", "anglais": "Anglais", "espagnol": "Espagnol",
    "francais": "Kreyòl", "histoire": "Sciences sociales", "svt": "Sciences exp.",
    "informatique": "Technologie", "art": "Art",
}


def get_gemini():
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "GEMINI_API_KEY manquante. Définis-la dans l'environnement Windows "
            "(pas DeepSeek). Ex: setx GEMINI_API_KEY \"AIza...\""
        )
    import google.generativeai as genai
    genai.configure(api_key=key)
    return genai.GenerativeModel(
        "gemini-2.0-flash",
        generation_config={"temperature": 0.1, "max_output_tokens": 8192},
    )


def chunks(text: str, size: int = 5000, overlap: int = 200) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    out, i = [], 0
    while i < len(text):
        out.append(text[i : i + size])
        i += size - overlap
    return out


def parse_json_array(text: str) -> list[dict]:
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def valid(q: dict) -> bool:
    opts = q.get("options") or []
    if len(opts) != 4:
        return False
    stem = (q.get("question") or "").strip()
    if len(stem) < 10:
        return False
    letter = str(q.get("correct", "")).strip().upper()[:1]
    if letter not in "ABCD":
        return False
    if len(str(q.get("explanation", ""))) < 15:
        return False
    for o in opts:
        if not str(o).strip() or len(str(o)) > 150:
            return False
    q["correct"] = letter
    q["options"] = [str(o).strip() for o in opts]
    q["question"] = stem
    q["difficulty"] = q.get("difficulty") if q.get("difficulty") in ("facile", "moyen", "difficile") else "moyen"
    try:
        q["timer_seconds"] = int(q.get("timer_seconds") or 30)
    except ValueError:
        q["timer_seconds"] = 30
    q["category"] = str(q.get("category") or "9e AF")[:80]
    return True


EXTRACT_PROMPT = """Tu es professeur Haïti 9e AF ({subject}).

EXTRAIT OCR d'examen OFFICIEL (bruité). Extrais les QCM avec 4 options visibles.
- Recopie énoncé + 4 options (corrige OCR mineur, ne change pas le sens).
- correct: lettre A-D UNIQUEMENT si tu es certain comme en correction d'examen.
- Si illisible, option manquante, ou réponse incertaine: IGNORE.
- N'invente AUCUNE question absente de l'extrait.
- explanation: 2-4 phrases en français (kreyòl si matière kreyòl).
- category, difficulty (facile/moyen/difficile), timer_seconds 25-45.

JSON array seul:
[{{"question":"...","options":["..","..","..",".."],"correct":"B","explanation":"...","category":"...","difficulty":"moyen","timer_seconds":30}}]

EXTRAIT:
{excerpt}
"""

SOLVE_PROMPT = """Tu es professeur Haïti 9e AF ({subject}).

Voici des QCM OFFICIELS extraits d'examens (sans corrigé). Pour CHAQUE item,
donne la bonne réponse si tu es CERTAIN (résous maths, faits histoire/SVT, grammaire).

Si tu n'es pas sûr à 100%, OMET l'item.

JSON array (même ordre que les items fournis, sous-ensemble seulement):
[{{"question":"...","options":["..","..","..",".."],"correct":"C","explanation":"...","category":"...","difficulty":"moyen","timer_seconds":30}}]

ITEMS:
{items}
"""


def call_model(model, prompt: str) -> list[dict]:
    try:
        resp = model.generate_content(prompt)
        text = (resp.text or "").strip()
        return parse_json_array(text)
    except Exception as e:
        print(f"    Gemini err: {e}")
        return []


def load_existing(path: Path, kind: str) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if kind == "array":
        return data if isinstance(data, list) else data.get("quiz", [])
    return data.get("quiz", []) if isinstance(data, dict) else []


def save(path: Path, kind: str, label: str, questions: list[dict], prefix: str) -> None:
    for i, q in enumerate(questions, 1):
        q["id"] = f"{prefix}{i}"
    if kind == "array":
        payload = questions
    else:
        payload = {
            "matiere": path.stem.replace("quiz_", "").replace("_9e", ""),
            "level": "9e AF",
            "total": len(questions),
            "description": f"Quiz {label} — QCM examens officiels 9e AF (Gemini vérifié)",
            "quiz": questions,
        }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def process_subject(model, subj: str, from_candidates: bool) -> int:
    fname, kind = OUT_FILES[subj]
    out_path = DB / fname
    label = LABELS[subj]
    prefix = PREFIX[subj]
    existing = load_existing(out_path, kind)
    seen = {(q.get("question") or "")[:80].lower() for q in existing}
    new: list[dict] = []

    if from_candidates:
        data = json.loads(CAND.read_text(encoding="utf-8"))
        raw = data.get(subj) or []
        batch_size = 12
        for i in range(0, len(raw), batch_size):
            batch = raw[i : i + batch_size]
            items_json = json.dumps(batch, ensure_ascii=False, indent=2)
            prompt = SOLVE_PROMPT.format(subject=label, items=items_json)
            print(f"  solve batch {i//batch_size + 1}/{(len(raw)+batch_size-1)//batch_size}")
            for q in call_model(model, prompt):
                if not valid(q):
                    continue
                key = q["question"][:80].lower()
                if key in seen:
                    continue
                seen.add(key)
                new.append(q)
            time.sleep(1.2)
    else:
        exam_path = JSON_DIR / f"exams_9e_{subj}.json"
        if not exam_path.exists():
            print(f"SKIP {subj}")
            return 0
        exam_data = json.loads(exam_path.read_text(encoding="utf-8"))
        for exam in exam_data.get("exams", []):
            t = exam.get("text") or ""
            fn = (exam.get("file") or "").lower()
            if subj == "francais" and "francais-2010" in fn:
                continue
            if len(t) < 200:
                continue
            parts = chunks(t, 4800)
            print(f"  exam {fn[:40]} chunks={len(parts)}")
            for ci, part in enumerate(parts):
                print(f"    chunk {ci+1}/{len(parts)}")
                prompt = EXTRACT_PROMPT.format(subject=label, excerpt=part)
                for q in call_model(model, prompt):
                    if not valid(q):
                        continue
                    key = q["question"][:80].lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    new.append(q)
                time.sleep(1.2)

    merged = existing + new
    save(out_path, kind, label, merged, prefix)
    print(f"  => {out_path.name} total={len(merged)} (+{len(new)})")
    return len(new)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--from-candidates", action="store_true")
    args = ap.parse_args()

    model = get_gemini()
    subs = list(OUT_FILES.keys())
    if args.subject:
        subs = [args.subject] if args.subject in OUT_FILES else subs
    if not args.all and not args.subject:
        ap.error("specify --subject or --all")

    for subj in subs:
        print(f"\n=== {subj} ===")
        process_subject(model, subj, args.from_candidates)


if __name__ == "__main__":
    main()
