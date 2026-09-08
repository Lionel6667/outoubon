"""Extract English grammar MCQs (3-4 options) from 9e anglais OCR packets."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKETS = ROOT / "database" / "_9e_packets"
CAND = ROOT / "database" / "_mcq_candidates_9e.json"
CLEAN = ROOT / "database" / "_mcq_clean_9e.json"
SOURCE = "exam_anglais_anglais-2011-2023-9e-af.pdf"
YEAR = "2011-2023"


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+\d+$", "", s)
    s = re.sub(r"^[|©©;'\u2019\u00e0\u00e8dfrS!>\s]+", "", s)
    s = re.sub(r"\s+[a-zA-Z]$", "", s)
    return s.strip(" .;|")


def split_inline_opts(line: str) -> list[str] | None:
    m = re.search(
        r"a\.\s+(.+?)\s+b\.\s+(.+?)\s+c\.\s+(.+?)(?:\s+d\.\s+(.+?))?(?:\s*$|\s+[|])",
        line,
        re.I,
    )
    if not m:
        return None
    opts = [clean(m.group(i)) for i in range(1, 5) if m.group(i)]
    return opts if len(opts) in (3, 4) else None


def parse_paren(stem: str, inner: str) -> tuple[str, list[str]] | None:
    parts = [clean(p) for p in re.split(r",\s*", inner) if clean(p)]
    if len(parts) not in (3, 4):
        return None
    if any(len(p) > 55 for p in parts):
        return None
    stem = clean(stem)
    if len(stem) < 8:
        return None
    return stem, parts


def extract_all() -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    def add(n: str, stem: str, opts: list[str]) -> None:
        stem = clean(stem)
        opts = [clean(o) for o in opts]
        if len(opts) not in (3, 4):
            return
        if len(stem) < 8 or len(stem) > 220:
            return
        if any(len(o) < 1 or len(o) > 80 for o in opts):
            return
        if re.match(r"^a\.\s", stem, re.I):
            return
        k = stem[:70].lower()
        if k in seen:
            return
        seen.add(k)
        found.append(
            {
                "n": n,
                "question": stem,
                "options": opts,
                "source": SOURCE,
                "year": YEAR,
            }
        )

    for fp in sorted(PACKETS.glob("anglais_*.txt")):
        lines = fp.read_text(encoding="utf-8").splitlines()
        in_grammar = False
        i = 0
        pending_num: str | None = None
        pending_stem: list[str] = []
        pending_opts: list[str] = []

        def flush_multiline() -> None:
            nonlocal pending_num, pending_stem, pending_opts
            if pending_num and pending_stem and len(pending_opts) in (3, 4):
                add(pending_num, " ".join(pending_stem), pending_opts)
            pending_num = None
            pending_stem = []
            pending_opts = []

        while i < len(lines):
            raw = lines[i]
            low = raw.lower()
            if re.search(r"grammar section|ii- grammar|b- grammar|il\. grammar|it\. grammar", low):
                flush_multiline()
                in_grammar = True
                i += 1
                continue
            if in_grammar and re.search(
                r"vocabulary section|iii\.|iii-|iv\.|iv-|writing production|writing section|ti\. vocabulary|t \| iii",
                low,
            ):
                flush_multiline()
                in_grammar = False
                i += 1
                continue
            if not in_grammar:
                i += 1
                continue

            # skip non-MCQ grammar (verb charts, fill blanks without options)
            if re.search(r"give the simple past|fill in the chart|change the following|put the following words|complete the sentences with:\s*$|follow the instructions|fill in the blanks by using", low):
                flush_multiline()
                i += 1
                continue

            num_m = re.match(r"^\s*(\d{1,2})[\.\)]\s+(.+)$", raw)
            if num_m:
                flush_multiline()
                num, rest = num_m.group(1), num_m.group(2)
                inline = split_inline_opts(rest)
                if inline:
                    stem = re.split(r"\ba\.\s", rest, flags=re.I)[0]
                    add(num, stem, inline)
                    i += 1
                    continue
                paren = re.search(r"\(([^)]+)\)\s*(.*)$", rest)
                if paren:
                    parsed = parse_paren(rest[: paren.start()], paren.group(1))
                    if parsed:
                        stem, opts = parsed
                        tail = clean(paren.group(2))
                        if tail:
                            stem = clean(stem + " " + tail)
                        add(num, stem, opts)
                        i += 1
                        continue
                pending_num = num
                pending_stem = [rest]
                pending_opts = []
                i += 1
                continue

            opt_m = re.match(r"^\s*[a-dA-D]\.\s+(.+)$", raw)
            if opt_m and pending_num:
                line_opt = clean(opt_m.group(1))
                inline = split_inline_opts("a. " + raw.split(".", 1)[-1]) if "b." in raw.lower() else None
                if inline:
                    add(pending_num, " ".join(pending_stem), inline)
                    pending_num = None
                    pending_stem = []
                    pending_opts = []
                else:
                    pending_opts.append(line_opt)
                    if len(pending_opts) >= 4:
                        add(pending_num, " ".join(pending_stem), pending_opts[:4])
                        pending_num = None
                        pending_stem = []
                        pending_opts = []
                i += 1
                continue

            if pending_num and pending_stem and not pending_opts:
                if re.search(r"\(([^)]+)\)", raw):
                    paren = re.search(r"\(([^)]+)\)", raw)
                    parsed = parse_paren(" ".join(pending_stem + [raw[: paren.start()]]), paren.group(1))
                    if parsed:
                        add(pending_num, parsed[0], parsed[1])
                        pending_num = None
                        pending_stem = []
                elif not re.match(r"^\s*[a-zA-Z|]", raw):
                    pending_stem.append(raw)
                i += 1
                continue

            i += 1
        flush_multiline()
    return found


def filter_item(q: dict) -> bool:
    stem = q.get("question") or ""
    opts = q.get("options") or []
    if len(opts) not in (3, 4):
        return False
    if len(stem) < 8 or len(stem) > 220:
        return False
    if re.search(r"Questions?\s*\d", stem, re.I):
        return False
    blob = " | ".join(opts)
    if re.search(r"\bpts\b|\b\d+\s*pts", blob, re.I):
        return False
    for o in opts:
        if len(o) < 1 or len(o) > 80:
            return False
    return True


def main() -> None:
    items = extract_all()
    cand = json.loads(CAND.read_text(encoding="utf-8"))
    cand["anglais"] = items
    CAND.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")
    clean_items = [q for q in items if filter_item(q)]
    all_clean = json.loads(CLEAN.read_text(encoding="utf-8"))
    all_clean["anglais"] = clean_items
    CLEAN.write_text(json.dumps(all_clean, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"anglais extracted: {len(items)} -> clean: {len(clean_items)}")
    for q in clean_items:
        print(f"  [{len(q['options'])}] {q['question'][:75]}")


if __name__ == "__main__":
    main()
