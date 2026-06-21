"""
generate_ai_json.py
====================
Converts note_*.json files into atomized note_*_ai.json files for FlexSearch + AI context.

Each subchapter becomes multiple atomic blocks (one per field: definition, explanation,
step_by_step, examples, detailed_examples, methods, common_mistakes, summary).

EXCLUDED subjects (no JSON context needed):
    anglais, espagnol, informatique

Usage:
    python generate_ai_json.py           # generate all (except excluded)
    python generate_ai_json.py physique  # regenerate only physique
"""

import json
import re
import sys
from pathlib import Path

# Directory containing note_*.json files
DATABASE_DIR = Path(__file__).parent / "database"

# Subjects to SKIP (no JSON context needed for AI)
EXCLUDED_SUBJECTS = {"anglais", "espagnol", "informatique"}

# Map: subject key → source file → output file
SUBJECT_FILE_MAP = {
    "maths":       ("note_math.json",        "note_math_ai.json"),
    "physique":    ("note_physique.json",     "note_physique_ai.json"),
    "chimie":      ("note_de_Chimie.json",    "note_de_Chimie_ai.json"),
    "svt":         ("note_SVT.json",          "note_SVT_ai.json"),
    "economie":    ("note_economie.json",     "note_economie_ai.json"),
    "philosophie": ("note_philosophie.json",  "note_philosophie_ai.json"),
    "francais":    ("note_kreyol.json",       "note_kreyol_ai.json"),
    "art":         ("note_art.json",          "note_art_ai.json"),
    "histoire":    ("note_sc_social.json",    "note_sc_social_ai.json"),
}

# Field types in order — each becomes a separate atomic block
BLOCK_TYPES = [
    ("definition",       "definition"),
    ("explanation",      "explanation"),
    ("step_by_step",     "method"),
    ("examples",         "examples"),
    ("detailed_examples","detailed_examples"),
    ("methods",          "methods"),
    ("common_mistakes",  "common_mistakes"),
    ("summary",          "summary"),
]


def _strip_json_fence(raw: str) -> str:
    """Remove ```json ... ``` fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    return raw.strip()


def _load_note_json(filepath: Path) -> dict | None:
    """Load and parse a note JSON file, handling BOM and json fences."""
    try:
        raw = filepath.read_text(encoding="utf-8-sig", errors="replace")
        raw = _strip_json_fence(raw)
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract valid JSON via regex
        m = re.search(r'\{[\s\S]+\}', raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    except Exception:
        pass
    return None


def _slugify(text: str) -> str:
    """Convert text to a lowercase slug for IDs."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s-]+", "_", text)
    return text[:60]


def _content_to_str(value) -> str:
    """Convert a field value (string, list, etc.) to a plain string."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item.strip())
            elif isinstance(item, dict):
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    if value is None:
        return ""
    return str(value).strip()


def chunk_note(data: dict, subject: str) -> list[dict]:
    """
    Convert a note_*.json data dict into a list of atomic blocks.
    Returns list of dicts with id, chapter, subchapter, type, content, level, tags.
    """
    blocks = []
    chapters = data.get("chapters", [])

    for chap_idx, chapter in enumerate(chapters):
        chap_title = str(chapter.get("chapter_title") or chapter.get("title") or f"Chapitre {chap_idx+1}").strip()
        chap_slug  = _slugify(chap_title)

        # Chapter-level summary block (introduction + objectives)
        intro_parts = []
        if chapter.get("chapter_introduction"):
            intro_parts.append(str(chapter["chapter_introduction"]).strip())
        objectives = chapter.get("chapter_objectives", [])
        if objectives:
            intro_parts.append("Objectifs :\n" + "\n".join(f"- {o}" for o in objectives if isinstance(o, str)))

        if intro_parts:
            blocks.append({
                "id": f"{subject}_{chap_slug}_intro",
                "chapter": chap_title,
                "subchapter": chap_title,
                "type": "chapter_summary",
                "content": "\n\n".join(intro_parts),
                "level": 1,
                "tags": [subject, chap_title.lower()],
                "chapter_num": chap_idx + 1,
            })

        # Subchapter atomic blocks
        for sub in chapter.get("subchapters", []):
            sub_title = str(sub.get("title") or sub.get("subchapter_title") or "").strip()
            if not sub_title:
                continue

            sub_slug = _slugify(sub_title)
            base_id  = f"{subject}_{chap_slug}_{sub_slug}"
            tags     = [subject, chap_title.lower(), sub_title.lower()]

            for field, block_type in BLOCK_TYPES:
                value = sub.get(field)
                if not value:
                    continue
                content = _content_to_str(value)
                if not content:
                    continue

                blocks.append({
                    "id": f"{base_id}_{block_type[:3]}",
                    "chapter": chap_title,
                    "subchapter": sub_title,
                    "type": block_type,
                    "content": content,
                    "level": 3,
                    "tags": tags,
                    "chapter_num": chap_idx + 1,
                })

        # Chapter exercises (optional separate blocks)
        for ex_idx, ex in enumerate(chapter.get("chapter_exercises", [])):
            q = str(ex.get("question") or "").strip()
            s = str(ex.get("solution") or "").strip()
            if q and s:
                blocks.append({
                    "id": f"{subject}_{chap_slug}_ex{ex_idx+1}",
                    "chapter": chap_title,
                    "subchapter": f"Exercice {ex_idx+1}",
                    "type": "exercise",
                    "content": f"Question : {q}\nSolution : {s}",
                    "level": 2,
                    "tags": [subject, chap_title.lower(), "exercice", "bac"],
                    "chapter_num": chap_idx + 1,
                })

    return blocks


def generate_for_subject(subject: str, src_file: str, out_file: str, verbose: bool = True) -> bool:
    """Generate the _ai.json for a single subject."""
    src_path = DATABASE_DIR / src_file
    out_path = DATABASE_DIR / out_file

    if not src_path.exists():
        if verbose:
            print(f"  [SKIP] {src_file} not found")
        return False

    data = _load_note_json(src_path)
    if not data:
        if verbose:
            print(f"  [ERROR] Failed to parse {src_file}")
        return False

    blocks = chunk_note(data, subject)
    if not blocks:
        if verbose:
            print(f"  [WARN] No blocks generated for {subject}")
        return False

    output = {
        "subject": subject,
        "source_file": src_file,
        "total_blocks": len(blocks),
        "blocks": blocks,
    }

    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    if verbose:
        print(f"  [OK] {subject}: {len(blocks)} blocks → {out_file}")
    return True


def main():
    targets = sys.argv[1:]  # optional: specific subject(s) to regenerate

    if targets:
        # Filter to requested subjects only
        to_process = {k: v for k, v in SUBJECT_FILE_MAP.items() if k in targets}
        if not to_process:
            print(f"Unknown subject(s): {targets}")
            print(f"Valid: {list(SUBJECT_FILE_MAP.keys())}")
            sys.exit(1)
    else:
        to_process = SUBJECT_FILE_MAP

    print(f"Generating AI JSON files for {len(to_process)} subject(s)...\n")
    success = 0
    for subject, (src, out) in to_process.items():
        if subject in EXCLUDED_SUBJECTS:
            print(f"  [SKIP] {subject} (excluded — no JSON context)")
            continue
        if generate_for_subject(subject, src, out):
            success += 1

    print(f"\nDone: {success}/{len(to_process)} generated in {DATABASE_DIR}")


if __name__ == "__main__":
    main()
