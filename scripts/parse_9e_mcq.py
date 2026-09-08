"""Parse 4-option MCQs from OCR exam text (no API)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "database" / "json"
OUT = ROOT / "database" / "_mcq_candidates_9e.json"

Q_SPLIT = re.compile(
    r"(?:^|\n)\s*(?:Q(?:uestion)?\s*)?(\d{1,2})\s*[\.\-–\)]\s+",
    re.I,
)
OPT_LINE = re.compile(
    r"(?:^|\n)\s*([a-dA-D])\s*[.)\-]\s+(.+?)(?=(?:\n\s*[a-dA-D]\s*[.)\-])|\n\s*\d{1,2}\s*[\.\-–\)]|\Z)",
    re.S,
)


def parse_text(text: str) -> list[dict]:
    text = text.replace("\r", "")
    # Normalize some OCR option markers
    text = re.sub(r"(?m)^\s*[|]\s*([a-dA-D])\s*[.)]", r"\n\1) ", text)
    found = []
    parts = Q_SPLIT.split(text)
    # parts: [preamble, num, body, num, body, ...]
    i = 1
    while i + 1 < len(parts):
        num, body = parts[i], parts[i + 1]
        i += 2
        opts = {}
        for m in OPT_LINE.finditer("\n" + body):
            letter = m.group(1).upper()
            val = re.sub(r"\s+", " ", m.group(2)).strip()
            val = re.sub(r"\(\d+\s*pts?\)", "", val, flags=re.I).strip(" .;|")
            if 1 <= len(val) <= 180:
                opts[letter] = val
        if len(opts) < 4:
            continue
        # stem = body before first option
        stem = OPT_LINE.split("\n" + body, maxsplit=1)[0]
        stem = re.sub(r"\s+", " ", stem).strip(" .;|")
        if len(stem) < 12 or len(stem) > 400:
            continue
        if not all(k in opts for k in "ABCD"):
            # sometimes only a,b,c and d later
            if set(opts.keys()) != set("ABCD"):
                continue
        found.append({
            "n": num,
            "question": stem,
            "options": [opts["A"], opts["B"], opts["C"], opts["D"]],
        })
    return found


def main() -> None:
    all_items = {}
    for path in sorted(JSON_DIR.glob("exams_9e_*.json")):
        subj = path.stem.replace("exams_9e_", "")
        data = json.loads(path.read_text(encoding="utf-8"))
        items = []
        for exam in data.get("exams", []):
            fname = exam.get("file", "")
            t = exam.get("text") or ""
            parsed = parse_text(t)
            for q in parsed:
                q["source"] = fname
                q["year"] = exam.get("year", "")
            items.extend(parsed)
        # dedupe by question prefix
        seen = set()
        uniq = []
        for q in items:
            k = q["question"][:70].lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(q)
        all_items[subj] = uniq
        print(f"{subj:16} {len(uniq):4} MCQ 4-options")
    OUT.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
