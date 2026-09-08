"""Extract 9e AF exam PDFs to database/json/exams_9e_{subject}.json (NS4 shape)."""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database"
OUT = DB / "json"
OUT.mkdir(parents=True, exist_ok=True)

# filename stem prefix → subject (same mapping as NS4 folders)
SUBJECT_FROM_PREFIX = {
    "exam_anglais_": "anglais",
    "exam_francais_": "francais",
    "exam_espagnol_": "espagnol",
    "exam_maths_": "maths",
    "exam_histoire_": "histoire",
    "exam_svt_": "svt",
    "exam_informatique_": "informatique",
    "exam_art_": "art",
}


def subject_of(name: str) -> str | None:
    n = name.lower()
    for prefix, subj in SUBJECT_FROM_PREFIX.items():
        if n.startswith(prefix):
            return subj
    return None


def detect_year(filename: str) -> str:
    years = re.findall(r"(20\d{2})", filename)
    if not years:
        return ""
    if "2010-2023" in filename or "2011-2023" in filename or "2014-2023" in filename:
        return f"{years[0]}-{years[-1]}" if len(set(years)) > 1 else years[0]
    return years[-1]


def extract_pdf(path: Path) -> tuple[str, int, str]:
    """Return (text, page_count, method)."""
    doc = pymupdf.open(str(path))
    pages = []
    for page in doc:
        t = (page.get_text("text") or "").strip()
        pages.append(t)
    doc.close()
    text = "\n\n".join(p for p in pages if p)
    method = "pymupdf_text" if text.strip() else "empty_scan"
    return text, len(pages), method


def main() -> None:
    files = sorted(DB.glob("examens_*/*9e*.pdf"))
    by_subject: dict[str, list] = defaultdict(list)
    print(f"{len(files)} PDFs")
    for p in files:
        subj = subject_of(p.name)
        if not subj:
            print(f"SKIP unknown {p.name}")
            continue
        text, n_pages, method = extract_pdf(p)
        print(f"  {p.name}: pages={n_pages} chars={len(text)} method={method}")
        by_subject[subj].append({
            "file": p.name,
            "year": detect_year(p.name),
            "series": ["9e AF"],
            "chars": len(text),
            "pages": n_pages,
            "extract_method": method,
            "text": text,
            "needs_ocr": method == "empty_scan",
        })

    for subj, exams in by_subject.items():
        total_chars = sum(e["chars"] for e in exams)
        data = {
            "subject": subj,
            "level": "9e AF",
            "generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "total_files": len(exams),
            "total_chars": total_chars,
            "exams": exams,
        }
        out = OUT / f"exams_9e_{subj}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"WROTE {out.name} files={len(exams)} chars={total_chars}")


if __name__ == "__main__":
    main()
