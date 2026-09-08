"""OCR scanned 9e AF exam PDFs (two-column) and refresh exams_9e_*.json."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database"
JSON_DIR = DB / "json"
OCR_DIR = DB / "_ocr_9e"
TESSDATA = DB / "_tessdata"
TESS = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
OCR_DIR.mkdir(parents=True, exist_ok=True)

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
    if any(x in filename for x in ("2010-2023", "2011-2023", "2014-2023")):
        return f"{years[0]}-{years[-1]}" if len(set(years)) > 1 else years[0]
    return years[-1]


def native_text(path: Path) -> str:
    doc = pymupdf.open(str(path))
    parts = [(page.get_text("text") or "").strip() for page in doc]
    doc.close()
    return "\n\n".join(p for p in parts if p)


def ocr_page_column(pix_path: Path, lang: str) -> str:
    cmd = [
        str(TESS),
        str(pix_path),
        "stdout",
        "-l", lang,
        "--tessdata-dir", str(TESSDATA),
        "--psm", "6",
        "-c", "preserve_interword_spaces=1",
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    return (r.stdout or b"").decode("utf-8", errors="replace")


def ocr_pdf(path: Path, lang: str = "fra") -> str:
    cache = OCR_DIR / f"{path.stem}.txt"
    if cache.exists() and cache.stat().st_size > 200:
        print(f"  cache {cache.name}")
        return cache.read_text(encoding="utf-8", errors="replace")

    doc = pymupdf.open(str(path))
    pages_out = []
    zoom = pymupdf.Matrix(2.2, 2.2)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        for i, page in enumerate(doc):
            w = page.rect.width
            clips = [
                pymupdf.Rect(page.rect.x0, page.rect.y0, page.rect.x0 + w * 0.52, page.rect.y1),
                pymupdf.Rect(page.rect.x0 + w * 0.48, page.rect.y0, page.rect.x1, page.rect.y1),
            ]
            col_txt = []
            for ci, clip in enumerate(clips):
                pix = page.get_pixmap(matrix=zoom, clip=clip, alpha=False)
                img = tmp_p / f"p{i+1}_{ci}.png"
                pix.save(str(img))
                col_txt.append(ocr_page_column(img, lang).strip())
            pages_out.append(f"\n----- PAGE {i+1} -----\n" + "\n\n".join(t for t in col_txt if t))
            print(f"    page {i+1}/{doc.page_count}")
    doc.close()
    text = "\n".join(pages_out)
    cache.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    if not TESS.exists():
        raise SystemExit(f"Tesseract introuvable: {TESS}")
    files = sorted(DB.glob("examens_*/*9e*.pdf"))
    by_subject: dict[str, list] = defaultdict(list)
    print(f"{len(files)} PDFs  tessdata={TESSDATA}")
    for p in files:
        subj = subject_of(p.name)
        if not subj:
            continue
        print(f"\n[{subj}] {p.name}")
        t0 = time.time()
        native = native_text(p)
        if len(native) > 800:
            text, method, n_pages = native, "pymupdf_text", None
            doc = pymupdf.open(str(p))
            n_pages = doc.page_count
            doc.close()
        else:
            lang = "fra+eng" if TESSDATA.joinpath("eng.traineddata").exists() else "fra"
            # Haitian Creole pages still OCR better with French
            if "kreyol" in p.name.lower() or "creole" in p.name.lower():
                lang = "fra"
            text = ocr_pdf(p, lang="fra")
            doc = pymupdf.open(str(p))
            n_pages = doc.page_count
            doc.close()
            method = "tesseract_fra"
        print(f"  chars={len(text)} method={method} {time.time()-t0:.1f}s")
        by_subject[subj].append({
            "file": p.name,
            "year": detect_year(p.name),
            "series": ["9e AF"],
            "chars": len(text),
            "pages": n_pages,
            "extract_method": method,
            "text": text,
            "needs_ocr": method.startswith("tesseract"),
        })

    for subj, exams in by_subject.items():
        data = {
            "subject": subj,
            "level": "9e AF",
            "generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "total_files": len(exams),
            "total_chars": sum(e["chars"] for e in exams),
            "exams": exams,
        }
        out = JSON_DIR / f"exams_9e_{subj}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"WROTE {out.name} chars={data['total_chars']}")


if __name__ == "__main__":
    main()
