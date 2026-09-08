"""Download official 9e AF exams from ExamHaiti into database/examens_* (NS4 layout)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# (exam page URL, dest folder, filename stem) — same convention as NS4
EXAMS = [
    (
        "https://www.examhaiti.com/anglais-2011-2023-9e-af/anglais-2011-2023-9e-af",
        "anglais",
        "exam_anglais_anglais-2011-2023-9e-af",
    ),
    (
        "https://www.examhaiti.com/creole-9e-af/kominikasyon-kreyol-9e-af-2025",
        "francais",
        "exam_francais_kominikasyon-kreyol-9e-af-2025",
    ),
    (
        "https://www.examhaiti.com/anglais-2011-2023-9e-af/creole-2014-2023-9e-af",
        "francais",
        "exam_francais_creole-2014-2023-9e-af",
    ),
    (
        "https://www.examhaiti.com/espagnol-9e-af/espagnol-9e-af-2025",
        "espagnol",
        "exam_espagnol_espagnol-9e-af-2025",
    ),
    (
        "https://www.examhaiti.com/anglais-2011-2023-9e-af/espagnol-2010-2023-9e-af",
        "espagnol",
        "exam_espagnol_espagnol-2010-2023-9e-af",
    ),
    (
        "https://www.examhaiti.com/francais-9e-af/francais-2010-2023-9e-af",
        "francais",
        "exam_francais_francais-2010-2023-9e-af",
    ),
    (
        "https://www.examhaiti.com/math-9e-af/math-9e-af-2025",
        "maths",
        "exam_maths_math-9e-af-2025",
    ),
    (
        "https://www.examhaiti.com/math-9e-af/math-2010-2023-9e-af",
        "maths",
        "exam_maths_math-2010-2023-9e-af",
    ),
    (
        "https://www.examhaiti.com/sciences-sociales-9e-af/science-sociales-2010-2023-9e-af",
        "histoire",
        "exam_histoire_science-sociales-2010-2023-9e-af",
    ),
    (
        "https://www.examhaiti.com/sciences-experimentales-9e-af/sciences-experimentales-2010-2023-9e-af",
        "svt",
        "exam_svt_sciences-experimentales-2010-2023-9e-af",
    ),
    (
        "https://www.examhaiti.com/autres/education-a-la-technologie-9e-af-2025",
        "informatique",
        "exam_informatique_education-a-la-technologie-9e-af-2025",
    ),
    (
        "https://www.examhaiti.com/autres/education-esthetique-et-artistique-9e-af-2025",
        "art",
        "exam_art_education-esthetique-et-artistique-9e-af-2025",
    ),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(
            url,
            headers=HEADERS,
            timeout=(20, 90),
            stream=True,
            allow_redirects=True,
        ) as r:
            r.raise_for_status()
            first = b""
            size = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    if not first:
                        first = chunk[:8]
                    f.write(chunk)
                    size += len(chunk)
                    if size > 50 * 1024 * 1024:
                        break
            if not first.startswith(b"%PDF") or size < 1000:
                log(f"    NOT A PDF ({r.url} type={r.headers.get('content-type')} size={size})")
                tmp.unlink(missing_ok=True)
                return False
            tmp.replace(dest)
            log(f"    OK {dest.name} ({size // 1024} KB) from {r.url}")
            return True
    except Exception as e:
        log(f"    ERR {e}")
        tmp.unlink(missing_ok=True)
        return False


def main() -> int:
    ok = 0
    session_note = f"Destination: {DB}"
    log(session_note)
    for url, folder, stem in EXAMS:
        dest = DB / f"examens_{folder}" / f"{stem}.pdf"
        if dest.exists() and dest.stat().st_size > 1000:
            log(f"[SKIP] {dest.relative_to(ROOT)}")
            ok += 1
            continue
        log(f"[DL] {stem} -> examens_{folder}/")
        if download(url, dest):
            ok += 1
        time.sleep(1.0)
    log(f"Done: {ok}/{len(EXAMS)} files")
    return 0 if ok == len(EXAMS) else 1


if __name__ == "__main__":
    sys.exit(main())
