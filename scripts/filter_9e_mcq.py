"""Keep only clean 4-option MCQs (no OCR junk in options)."""
from __future__ import annotations

import json
import re
from pathlib import Path

src = Path("database/_mcq_candidates_9e.json")
out = Path("database/_mcq_clean_9e.json")
data = json.loads(src.read_text(encoding="utf-8"))


def clean(q: dict) -> bool:
    stem = q.get("question") or ""
    opts = q.get("options") or []
    if len(opts) != 4:
        return False
    if len(stem) < 15 or len(stem) > 280:
        return False
    if re.search(r"Questions?\s*\d", stem, re.I):
        return False
    blob = " | ".join(opts)
    if re.search(r"\bpts\b|\b\d+\s*pts", blob, re.I):
        return False
    if re.search(r"\b[a-d]\s*[.)]\s+[a-z]", blob):
        return False
    for o in opts:
        if len(o) < 1 or len(o) > 90:
            return False
        if o.count("|") > 2:
            return False
    # drop leftover option letters glued in stem/options
    if stem.lower().rstrip().endswith((" a", " b", " c", " d")):
        return False
    return True


cleaned = {}
for subj, items in data.items():
    keep = [q for q in items if clean(q)]
    cleaned[subj] = keep
    print(f"{subj:16} {len(items):3} -> {len(keep):3}")

out.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
print("WROTE", out)
