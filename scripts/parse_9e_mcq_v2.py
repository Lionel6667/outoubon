"""Aggressive MCQ extraction from 9e OCR exam text (no API)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "database" / "json"
OUT = ROOT / "database" / "_mcq_candidates_9e.json"

# Split exams: Maths 1, Anglais 12, etc.
EXAM_SPLIT = re.compile(
    r"(?m)(?:^|\n)(?:Maths|Anglais|Espagnol|Francais|Kreyol|Histoire|Sciences|"
    r"Art|Techno|Informatique)\s+\d+|----- PAGE \d+ -----",
    re.I,
)

OPT = re.compile(
    r"(?:^|\n)\s*([a-dA-D])\s*[.)]\s+(.+?)(?=(?:\n\s*[a-dA-D]\s*[.)])|\n\s*\d{1,2}\s*[\.\-–\)]|\Z)",
    re.S,
)

Q_START = re.compile(
    r"(?:^|\n)\s*(\d{1,2})\s*[\.\-–\)]\s+(.+?)(?=(?:\n\s*[a-dA-D]\s*[.)])|\n\s*\d{1,2}\s*[\.\-–\)]|\Z)",
    re.S,
)

# English grammar: "1. Pepita ... a. call b. called c. will call"
ENG_GRAMMAR = re.compile(
    r"(?:^|\n)\s*(\d{1,2})\s*[\.\-]\s+(.+?)\s+"
    r"(?:^|\n)\s*a\.\s+(.+?)\s+(?:^|\n)\s*b\.\s+(.+?)\s+(?:^|\n)\s*c\.\s+(.+?)"
    r"(?:\s+(?:^|\n)\s*d\.\s+(.+?))?(?=(?:\n\s*\d{1,2}\s*[\.\-])|\Z)",
    re.S | re.I,
)

JUNK_OPT = re.compile(
    r"^(pts|deuxième partie|première partie|réponds|questions?\s+\d|grammar|vocabulary|"
    r"writing|comprehension|matiques|\d+\s*pts?)$",
    re.I,
)


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\(\d+\s*pts?\)", "", s, flags=re.I)
    s = re.sub(r"\s*[|]\s*$", "", s)
    s = re.sub(r"\s+[a-dA-D]\s*$", "", s)
    return s.strip(" .;|—-")


def parse_block(text: str) -> list[dict]:
    found: list[dict] = []
    # Standard numbered + a/b/c/d
    for m in Q_START.finditer(text):
        num, body = m.group(1), m.group(2)
        opts = {}
        for om in OPT.finditer("\n" + body):
            letter = om.group(1).upper()
            val = clean(om.group(2))
            if 1 <= len(val) <= 120 and not JUNK_OPT.match(val):
                opts[letter] = val
        if len(opts) < 4:
            continue
        if not all(k in opts for k in "ABCD"):
            continue
        stem_part = OPT.split("\n" + body, maxsplit=1)[0]
        stem = clean(stem_part)
        if len(stem) < 10 or len(stem) > 350:
            continue
        found.append({
            "n": num,
            "question": stem,
            "options": [opts["A"], opts["B"], opts["C"], opts["D"]],
        })

    # English 3-option grammar (pad with duplicate only if 4th exists elsewhere - skip 3)
    for m in ENG_GRAMMAR.finditer(text):
        num = m.group(1)
        stem = clean(m.group(2))
        oa, ob, oc = clean(m.group(3)), clean(m.group(4)), clean(m.group(5))
        od = clean(m.group(6) or "")
        if not od:
            continue
        if len(stem) < 8:
            continue
        found.append({
            "n": num,
            "question": stem,
            "options": [oa, ob, oc, od],
            "kind": "anglais_grammar",
        })
    return found


def parse_text(text: str) -> list[dict]:
    text = text.replace("\r", "")
    parts = EXAM_SPLIT.split(text)
    all_q: list[dict] = []
    for part in parts:
        if len(part.strip()) < 80:
            continue
        all_q.extend(parse_block(part))
    if not all_q:
        all_q = parse_block(text)
    return all_q


def main() -> None:
    all_items: dict[str, list] = {}
    for path in sorted(JSON_DIR.glob("exams_9e_*.json")):
        subj = path.stem.replace("exams_9e_", "")
        data = json.loads(path.read_text(encoding="utf-8"))
        items: list[dict] = []
        for exam in data.get("exams", []):
            fname = exam.get("file", "")
            t = exam.get("text") or ""
            if subj == "francais" and "francais-2010" in fname.lower():
                continue  # français langue ≠ kreyòl quiz bank
            parsed = parse_text(t)
            for q in parsed:
                q["source"] = fname
                q["year"] = exam.get("year", "")
            items.extend(parsed)
        seen: set[str] = set()
        uniq: list[dict] = []
        for q in items:
            k = q["question"][:72].lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(q)
        all_items[subj] = uniq
        print(f"{subj:16} {len(uniq):4} MCQ")

    OUT.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
