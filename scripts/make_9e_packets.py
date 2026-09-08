"""Split 9e OCR into ~9k-char packets for Gemini web extraction."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "database" / "json"
OUT = ROOT / "database" / "_9e_packets"
MAXC = 8500


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.txt"):
        old.unlink()
    for path in sorted(JSON_DIR.glob("exams_9e_*.json")):
        subj = path.stem.replace("exams_9e_", "")
        data = json.loads(path.read_text(encoding="utf-8"))
        n = 0
        for exam in data.get("exams") or []:
            fname = exam.get("file", "")
            pages = re.split(r"\n----- PAGE \d+ -----\n", exam.get("text") or "")
            buf = ""
            for i, pg in enumerate(pages):
                pg = pg.strip()
                if len(pg) < 80:
                    continue
                if buf and len(buf) + len(pg) > MAXC:
                    n += 1
                    (OUT / f"{subj}_{n:02d}.txt").write_text(buf, encoding="utf-8")
                    buf = ""
                buf += f"\n\n===== PAGE {i} / {fname} =====\n{pg}"
            if buf.strip():
                n += 1
                (OUT / f"{subj}_{n:02d}.txt").write_text(buf, encoding="utf-8")
        print(subj, "packets", n)
    print("total", len(list(OUT.glob("*.txt"))))


if __name__ == "__main__":
    main()
