"""Extract MCQs from OCR text using sliding window over a/b/c/d blocks."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "database" / "json"
OUT = ROOT / "database" / "_mcq_candidates_9e.json"

# Block: optional question number + stem + a. b. c. d.
BLOCK = re.compile(
    r"(?:^|\n)\s*(?:(\d{1,2})\s*[\.\-–\)]\s+)?(.+?)"
    r"(?:\n\s*a[\.\)]\s*(.+?))"
    r"(?:\n\s*b[\.\)]\s*(.+?))"
    r"(?:\n\s*c[\.\)]\s*(.+?))"
    r"(?:\n\s*d[\.\)]\s*(.+?))"
    r"(?=\n\s*(?:\d{1,2}\s*[\.\-–\)]|----- PAGE|Maths\s+\d|Anglais\s+\d|\Z))",
    re.S | re.I,
)


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\(\d+\s*pts?\)", "", s, flags=re.I)
    s = re.sub(r"\s*[|]\s*$", "", s)
    return s.strip(" .;|—-")


def bad_opt(s: str) -> bool:
    if len(s) < 1 or len(s) > 130:
        return True
    if re.search(r"deuxième partie|première partie|réponds aux", s, re.I):
        return True
    if re.match(r"^\d+\s*pts?$", s, re.I):
        return True
    return False


def parse_text(text: str) -> list[dict]:
    text = text.replace("\r", "")
    found = []
    for m in BLOCK.finditer(text):
        num, stem, oa, ob, oc, od = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
        stem = clean(stem)
        opts = [clean(oa), clean(ob), clean(oc), clean(od)]
        if len(stem) < 12 or len(stem) > 400:
            continue
        if any(bad_opt(o) for o in opts):
            continue
        if stem.lower().startswith(("complète", "réponds", "relie", "trouve dans la colonne")):
            if len(stem) < 25:
                continue
        found.append({
            "n": num or "",
            "question": stem,
            "options": opts,
        })
    return found


def main() -> None:
    all_items: dict[str, list] = {}
    for path in sorted(JSON_DIR.glob("exams_9e_*.json")):
        subj = path.stem.replace("exams_9e_", "")
        data = json.loads(path.read_text(encoding="utf-8"))
        items: list[dict] = []
        for exam in data.get("exams", []):
            fname = exam.get("file", "")
            if subj == "francais" and "francais-2010" in fname.lower():
                continue
            t = exam.get("text") or ""
            parsed = parse_text(t)
            for q in parsed:
                q["source"] = fname
                q["year"] = exam.get("year", "")
            items.extend(parsed)
        seen: set[str] = set()
        uniq: list[dict] = []
        for q in items:
            k = q["question"][:70].lower()
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
