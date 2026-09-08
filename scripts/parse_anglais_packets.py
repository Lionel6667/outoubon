"""Parse 4-option English grammar MCQs from _9e_packets anglais OCR."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKETS = ROOT / "database" / "_9e_packets"
CLEAN = ROOT / "database" / "_mcq_clean_9e.json"
CAND = ROOT / "database" / "_mcq_candidates_9e.json"


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+\d+$", "", s)
    s = re.sub(r"^[|©©;'\u2019\u00e0\u00e8dfrS!>\s]+", "", s)
    return s.strip(" .;|")


def parse_paren_block(num: str, lines: list[str]) -> dict | None:
    block = clean(" ".join(lines))
    m = re.search(r"\(([^)]+)\)", block)
    if not m:
        return None
    parts = [clean(p) for p in re.split(r",\s*", m.group(1)) if clean(p)]
    if len(parts) != 4:
        return None
    stem = clean(block[: m.start()] + " " + block[m.end() :])
    if len(stem) < 8 or len(stem) > 220:
        return None
    if any(len(o) < 1 or len(o) > 80 for o in parts):
        return None
    return {"n": num, "question": stem, "options": parts}


def parse_from_packets() -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    for fp in sorted(PACKETS.glob("anglais_*.txt")):
        lines = fp.read_text(encoding="utf-8").splitlines()
        in_grammar = False
        i = 0
        while i < len(lines):
            line = lines[i]
            low = line.lower()
            if "grammar section" in low or "grammar\r" in low or re.search(r"\bgrammar\b", low):
                in_grammar = True
                i += 1
                continue
            if in_grammar and re.search(r"vocabulary section|iii\.|iv\.|writing", low):
                in_grammar = False
                i += 1
                continue
            if not in_grammar:
                i += 1
                continue

            # numbered stem
            m = re.match(r"^\s*(\d{1,2})\.\s+(.+)$", line)
            if m:
                num, rest = m.group(1), m.group(2)
                chunk = [rest]
                j = i + 1
                # collect continuation until next numbered or option block done
                opts: list[str] = []
                opt_mode = False
                while j < len(lines):
                    nxt = lines[j]
                    if re.match(r"^\s*\d{1,2}\.\s+", nxt) and j > i + 1:
                        break
                    if re.search(r"vocabulary section|writing production|iii\.|iv\.", nxt.lower()):
                        break
                    om = re.match(r"^\s*[a-dA-D]\.\s+(.+)$", nxt)
                    if om:
                        opt_mode = True
                        opts.append(clean(om.group(1)))
                        j += 1
                        continue
                    # same-line options: a. x b. y c. z d. w
                    sm = re.search(
                        r"a\.\s+(.+?)\s+b\.\s+(.+?)\s+c\.\s+(.+?)\s+d\.\s+(.+?)$",
                        nxt,
                        re.I,
                    )
                    if sm:
                        opts = [clean(sm.group(k)) for k in range(1, 5)]
                        j += 1
                        break
                    if opt_mode and opts:
                        break
                    if not opt_mode:
                        chunk.append(nxt)
                    j += 1

                item = None
                if len(opts) == 4:
                    stem = clean(" ".join(chunk))
                    if "?" not in stem and stem.endswith("?"):
                        pass
                    if len(stem) >= 8:
                        item = {"n": num, "question": stem, "options": opts}
                else:
                    item = parse_paren_block(num, chunk)

                if item:
                    k = item["question"][:60].lower()
                    if k not in seen:
                        seen.add(k)
                        item["source"] = "exam_anglais_anglais-2011-2023-9e-af.pdf"
                        item["year"] = "2011-2023"
                        found.append(item)
                i = j
                continue
            i += 1
    return found


def main() -> None:
    items = parse_from_packets()
    cand = json.loads(CAND.read_text(encoding="utf-8"))
    cand["anglais"] = items
    CAND.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")

    # run filter inline
    from filter_9e_mcq import clean as filt_clean  # type: ignore

    keep = [q for q in items if filt_clean(q)]
    cleaned = json.loads(CLEAN.read_text(encoding="utf-8"))
    cleaned["anglais"] = keep
    CLEAN.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"anglais extracted: {len(items)} -> clean: {len(keep)}")
    for q in keep[:5]:
        print(" ", q["question"][:70])
        print("   ", q["options"])


if __name__ == "__main__":
    main()
