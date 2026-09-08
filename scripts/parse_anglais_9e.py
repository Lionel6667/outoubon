"""Parse English 9e grammar MCQs (3-4 options) from OCR text."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "database" / "_mcq_candidates_9e.json"

# Grammar: numbered stem then a. b. c. (optional d.)
GRAM = re.compile(
    r"(?:^|\n)\s*(\d{1,2})\s*[\.\-]\s+(.+?)"
    r"(?:\n\s*a\.\s+(.+?))"
    r"(?:\n\s*b\.\s+(.+?))"
    r"(?:\n\s*c\.\s+(.+?))"
    r"(?:\n\s*d\.\s+(.+?))?"
    r"(?=\n\s*\d{1,2}\s*[\.\-]|\n\s*[A-D]\.\s|\nII\.|\nIII\.|\Z)",
    re.S | re.I,
)

UNDERLINE = re.compile(
    r"Underline the correct word[^\n]*\n(.+?)(?=\n\s*\d+\.|$)",
    re.S | re.I,
)


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .;|")


def parse_anglais(text: str) -> list[dict]:
    text = text.replace("\r", "")
    found = []
    for m in GRAM.finditer(text):
        stem = clean(m.group(2))
        oa, ob, oc = clean(m.group(3)), clean(m.group(4)), clean(m.group(5))
        od = clean(m.group(6) or "")
        opts = [oa, ob, oc]
        if od:
            opts.append(od)
        if len(opts) < 3:
            continue
        if len(opts) == 3:
            continue  # need 4 for quiz schema
        if len(stem) < 8 or len(stem) > 200:
            continue
        if any(len(o) < 1 or len(o) > 80 for o in opts):
            continue
        found.append({"n": m.group(1), "question": stem, "options": opts})
    return found


def main() -> None:
    path = ROOT / "database" / "json" / "exams_9e_anglais.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for exam in data.get("exams", []):
        t = exam.get("text") or ""
        for q in parse_anglais(t):
            q["source"] = exam.get("file", "")
            q["year"] = exam.get("year", "")
            items.append(q)
    seen = set()
    uniq = []
    for q in items:
        k = q["question"][:60].lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(q)
    print(f"anglais grammar 4-opt: {len(uniq)}")
    # merge into candidates
    cand = json.loads(OUT.read_text(encoding="utf-8"))
    cand["anglais"] = uniq
    OUT.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
