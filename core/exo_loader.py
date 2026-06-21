"""
Loader for real BAC exercises from database/exo*.json files.

The files have FOUR different formats:

  exo_svt.json      – JSON list:   [{source, theme, enonce, questions[], reponses[]}]
  exo_physique.json – Markdown with 3 sub-formats across sections:
                        § Démonstrations:   **N.** *Source* : `file`  *Énoncé* : text
                        § Courant alt. etc: **N.** *Source* : `file` (ex) *Énoncé* : text\na)b)c)
                        § Chute libre etc:  **N. Source :** `file`  **Énoncé :**\ntext\na)b)c)
  exo_math.json     – Markdown: **N.** *Source*: ... *Thème*: ... *Intro*: ... *Questions*:\na)b)
  exo_chimie.json   – JSON dict {metadata, chapitres:[{id,titre,exercices:[{num,type,source,
                        enonce,questions[],reponses{}}]}]}
                      Chapters 11+ use embedded markdown (file is partially malformed).

All results are normalised to:
    {source, source_display, theme, chapter, subject, intro, enonce, questions, reponses}
"""

import json
import re
import random
from pathlib import Path

_DB_DIR = Path(__file__).resolve().parent.parent / 'database'

# subject key → (filename, parser_format)
_EXO_FILES: dict[str, tuple[str, str]] = {
    'svt':      ('exo_svt.json',      'json_list'),
    'physique': ('exo_physique.json', 'markdown_physique'),
    'maths':    ('exo_math.json',     'markdown_math'),
    'chimie':   ('exo_chimie.json',   'json_chapitres'),
}

# In-memory cache (populated once at first request)
_cache: dict[str, list] = {}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _backtick_source(block: str) -> str:
    """Extract `exam_*.pdf` filename from the block."""
    m = re.search(r'`(exam_[^`]+\.pdf)`', block)
    return m.group(1) if m else ''


def _year_from_source(s: str) -> str:
    if not s:
        return ''
    m = re.search(r'\b(20\d{2})\b', s)
    return m.group(1) if m else ''


def _source_display(source: str) -> str:
    source = source or ''
    year = _year_from_source(source)
    return f"Bac Haïti {year}" if year else source.replace('.pdf', '')


def _extract_sub_questions(text: str) -> tuple[str, list[str]]:
    """
    Split text into (intro, questions[]).
    Questions are lines starting with: a) b) 1) 2) 1. 2. a. b. etc.
    Returns original intro (before first question) and question list.
    """
    q_re = re.compile(
        r'^(?:[a-eA-E]\s*[\)\.]|[1-9]\s*[\)\.])\s+.+',
        re.MULTILINE,
    )
    matches = list(q_re.finditer(text))
    if not matches:
        return text.strip(), []

    first_pos = matches[0].start()
    intro = text[:first_pos].strip()
    questions = [m.group().strip() for m in matches if len(m.group().strip()) > 5]
    return intro, questions


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION & CLEANING
# ─────────────────────────────────────────────────────────────────────────────

def _clean_text_corruptions(text: str) -> str:
    """
    Remove common data corruption artifacts:
    - Replace alternative pipe characters (divides symbol U+2223) with proper pipe |
    - Remove control characters that break markdown rendering
    - Fix doubled punctuation and weird unicode characters from PDF extraction
    """
    if not text:
        return text
    
    # Replace alternative box-drawing / divides characters with proper pipe
    text = text.replace('∣', '|')  # U+2223 DIVIDES  → |
    text = text.replace('│', '|')  # U+2502 BOX DRAWINGS LIGHT VERTICAL → |
    text = text.replace('║', '|')  # U+2551 BOX DRAWINGS DOUBLE VERTICAL → |
    
    # Remove control characters  that corrupt markdown (but keep newlines and tabs)
    text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', text)
    
    # Fix common rendering artifacts from PDF extraction
    text = text.replace('−', '-')   # Minus sign → hyphen
    text = text.replace('–', '-')   # En-dash → hyphen
    
    # Remove duplicate spaces (keep single space)
    text = re.sub(r' {2,}', ' ', text)
    
    return text


def _validate_exercise(exo: dict) -> bool:
    """
    Validate an exercise dict to ensure it's not corrupted or incomplete.
    Returns True if valid, False if should be discarded.
    """
    if not isinstance(exo, dict):
        return False
    
    # Must have essential fields
    intro = (exo.get('intro') or '').strip()
    enonce = (exo.get('enonce') or '').strip()
    
    # At least one must be present and reasonably long (not just garbage)
    if not intro and not enonce:
        return False
    
    min_len = max(intro, enonce, key=len)
    if len(min_len) < 20:  # too short, likely corrupted
        return False
    
    # Check for corruption markers: too many special characters in a row
    # (indicates stripped/malformed content)
    if re.search(r'[|─=\-]{5,}', intro + enonce):
        # This might be a table separator ok, but combined with other markers = bad
        if re.search(r'[^\w\s\.\,\(\)\[\]\{\}\|\-─=:;\'\"@#$%&*+/\\À-ÿ]{3,}', intro + enonce):
            return False
    
    # Verify question count is reasonable
    questions = exo.get('questions', [])
    if not isinstance(questions, list):
        return False
    if len(questions) > 20:  # unreasonably many questions = likely corrupted
        return False
    
    # Theme/chapter must exist
    theme = (exo.get('theme') or exo.get('chapter') or '').strip()
    if not theme:
        return False
    
    return True


def _sanitize_exercise(exo: dict) -> dict:
    """
    Clean an exercise dict by fixing common data corruption issues.
    Returns the cleaned exercise.
    """
    # Clean text fields
    for field in ('intro', 'enonce', 'theme', 'chapter', 'source', 'source_display'):
        if field in exo and isinstance(exo[field], str):
            exo[field] = _clean_text_corruptions(exo[field]).strip()
    
    # Clean questions list
    if 'questions' in exo and isinstance(exo['questions'], list):
        exo['questions'] = [
            _clean_text_corruptions(q).strip() 
            for q in exo['questions'] 
            if isinstance(q, str)
        ]
    
    return exo


# ─────────────────────────────────────────────────────────────────────────────
# SERIES → TABLE  (statistics / probability exercises)
# ─────────────────────────────────────────────────────────────────────────────

def _split_series_values(vals_str: str, expected_n: int = 0) -> list[str]:
    """
    Split a French stats values string into a list.
    Try to match expected_n values if given, choosing ';' or ',' accordingly.
    French convention: commas can be decimal points, semicolons list separators.
    """
    vals_str = vals_str.strip()
    by_semi  = [v.strip() for v in vals_str.split(';')  if v.strip()]
    by_comma = [v.strip() for v in vals_str.split(',')  if v.strip()]
    if expected_n:
        if len(by_semi)  == expected_n:
            return by_semi
        if len(by_comma) == expected_n:
            return by_comma
    # Default: if the string contains ';', use it; otherwise ','
    return by_semi if ';' in vals_str else by_comma


def _series_to_md_table(text: str) -> str:
    """
    Detect statistics/probability series data in the intro text and convert
    to a markdown pipe table that renderMarkdown can render as an HTML table.

    Handles these patterns (LaTeX \\(...\\) wrappers, French decimal notation):
      A.  \\(x=1,2,3\\) ; \\(y=4,5,6\\)                  — values inside LaTeX
      B.  \\(x\\) : 1, 2, 3 ; \\(y\\) : 4, 5, 6          — colon outside LaTeX
      C.  \\(x\\) = 1, 2, 3 ; \\(y\\) = 4, 5, 6          — equals outside LaTeX
      D.  \\(x=0,5;1,5;2,5\\) ; \\(y=0;1;1,5\\)           — semicolon=list sep, comma=decimal
      E.  \\(x\\) (unit) = v1 ; v2 ; \\(y\\) (unit) = ... — unit in parens
      F.  prefix \\(x\\) (unit) = v1 ; v2 ; ...            — with text prefix

    Returns the text unchanged if no convertible series is found.
    """
    lower = text.lower()

    # Activate on known keywords OR on a detectable \\(var\\) = numeric_data pattern
    has_keywords = any(kw in lower for kw in (
        'série', 'serie', 'effectif', 'fréquence', 'frequence',
        'tableau', 'données', 'donnees',
    ))
    has_series_pattern = bool(re.search(
        r'\\\([a-zA-Z_]\w*\\\)\s*(?:\([^)]{0,30}\)\s*)?[=:]\s*[-\d,;. ]',
        text
    ))
    if not has_keywords and not has_series_pattern:
        return text

    rows: list[tuple[str, list[str], int, int]] = []  # (var, values, start, end)

    # ── Pass 1: \\(var=values\\) — var name IS inside the LaTeX ────────────
    for m in re.finditer(r'\\\(([a-zA-Z_]\w*)\s*=\s*([^)]+?)\\\)', text):
        var      = m.group(1)
        vals_str = m.group(2).strip()
        vals     = _split_series_values(vals_str)
        if len(vals) >= 2:
            rows.append((var, vals, m.start(), m.end()))

    # ── Pass 2: \\(var\\) : values — colon outside LaTeX ────────────────────
    if not rows:
        for m in re.finditer(
            r'\\\(([a-zA-Z_]\w*)\\\)\s*:\s*([\d,.; \-+]+?)(?=\s*;\s*\\\(|\s*[.!?]?\s*(?:\n|$))',
            text
        ):
            var      = m.group(1)
            vals_str = m.group(2).strip()
            vals     = _split_series_values(vals_str)
            if len(vals) >= 2:
                rows.append((var, vals, m.start(), m.end()))

    # ── Pass 2b: \\(var\\) (optional unit) = values — equals outside LaTeX ──
    # Handles: \\(x\\) = 1,2,3        \\(x\\) (cm) = 41,5 ; 42,5
    #          Série : \\(x\\) = 0,5 ; 1,5     text \\(x\\) (kg) = 4 ; 5,4
    # NOTE: backslash excluded from value chars to avoid consuming \\(next_var\\)
    # Runs when previous passes found fewer than 2 rows (including Pass 1 partial matches).
    if len(rows) < 2:
        rows_2b = []
        for m in re.finditer(
            r'\\\(([a-zA-Z_]\w*)\\\)'       # \\(var\\)
            r'(?:\s*\([^)]{0,30}\))?'        # optional (unit), max 30 chars
            r'\s*=\s*'                        # equals sign
            r'((?:[-\d,;. ])*)',             # values: digits / commas / semicolons / spaces (NO backslash)
            text
        ):
            var      = m.group(1)
            vals_raw = m.group(2).strip(' ;.')
            vals     = _split_series_values(vals_raw)
            if len(vals) >= 2:
                rows_2b.append((var, vals, m.start(), m.end()))
        if len(rows_2b) >= 2:
            rows = rows_2b

    # ── Pass 3: mixed — some \\(var=values\\), some \\(values_only\\) ──────
    if len(rows) < 2:
        rows = []
        for m in re.finditer(r'\\\(([^)]+)\\\)', text):
            expr = m.group(1).strip()
            if '=' in expr:
                eq_i     = expr.index('=')
                var      = expr[:eq_i].strip()
                vals_str = expr[eq_i+1:].strip()
            else:
                # values-only block: use the preceding word as label
                before_chunk = text[max(0, m.start()-30):m.start()].strip()
                label_m = re.search(r'(\w+)\s*$', before_chunk)
                var      = label_m.group(1) if label_m else 'val'
                vals_str = expr
            vals = _split_series_values(vals_str)
            if len(vals) >= 2:
                rows.append((var, vals, m.start(), m.end()))

    if len(rows) < 2:
        return text  # nothing useful found

    # ── Reconcile row lengths ────────────────────────────────────────────────
    # Cross-validate: use the most common length
    from collections import Counter
    counts = Counter(len(r[1]) for r in rows)
    best_n = counts.most_common(1)[0][0]
    valid_rows = []
    for var, vals, s, e in rows:
        if len(vals) == best_n:
            valid_rows.append((var, vals, s, e))
        else:
            # Re-try split with expected_n
            expr_m = re.search(re.escape(text[s:e]), text)
            if not expr_m:
                continue
            raw_expr = m.group(0) if (m := re.search(r'\\\(([^)]+)\\\)', text[s:e])) else ''
            if '=' in raw_expr:
                vals_str = raw_expr.split('=', 1)[1].rstrip('\\)')
            else:
                vals_str = raw_expr.lstrip('\\(').rstrip('\\)')
            new_vals = _split_series_values(vals_str.strip(), expected_n=best_n)
            if len(new_vals) == best_n:
                valid_rows.append((var, new_vals, s, e))

    if len(valid_rows) < 2:
        return text

    # ── Build pipe table ─────────────────────────────────────────────────────
    n       = best_n
    header  = '| | ' + ' | '.join(str(i) for i in range(1, n + 1)) + ' |'
    sep     = '|---|' + '---|' * n
    data_rs = ['| ' + r[0] + ' | ' + ' | '.join(r[1]) + ' |' for r in valid_rows]
    table_md = '\n'.join([header, sep] + data_rs)

    # ── Replace the series span in the text ─────────────────────────────────
    first_s = valid_rows[0][2]
    last_e  = valid_rows[-1][3]
    before  = text[:first_s].rstrip()
    after   = text[last_e:].lstrip(' .;')
    sep_nl  = '\n\n' if after.strip() else ''
    return before + '\n\n' + table_md + sep_nl + after


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT 1 — JSON list  (SVT)
# ─────────────────────────────────────────────────────────────────────────────

def _load_json_list(path: Path, subject: str) -> list[dict]:
    data = json.loads(path.read_text(encoding='utf-8'))
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        enonce = (item.get('enonce') or '').strip()
        if not enonce:
            continue
        questions = [str(q).strip() for q in (item.get('questions') or []) if str(q).strip()]
        source    = item.get('source', '')
        theme     = item.get('theme', '').strip()
        intro, qs_inline = _extract_sub_questions(enonce)
        if not questions:
            questions = qs_inline
        
        exo_dict = {
            'source':         source,
            'source_display': _source_display(source),
            'theme':          theme,
            'chapter':        theme,
            'subject':        subject,
            'intro':          intro or enonce,
            'enonce':         enonce,
            'questions':      questions,
            'reponses':       item.get('reponses', []),
        }
        
        # CLEAN and VALIDATE before adding
        exo_dict = _sanitize_exercise(exo_dict)
        if _validate_exercise(exo_dict):
            result.append(exo_dict)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT 2 — Markdown (Physique)
# ─────────────────────────────────────────────────────────────────────────────

def _split_md_sections(text: str) -> list[tuple[str, str]]:
    """Return [(chapter_title, body_text)] split at '## ' headings."""
    sections: list[tuple[str, str]] = []
    cur_title = ''
    cur_lines: list[str] = []
    for line in text.split('\n'):
        m = re.match(r'^##\s+(.+)', line)
        if m:
            if cur_title:
                sections.append((cur_title, '\n'.join(cur_lines)))
            # Strip "(N exercices...)" suffix in all variants
            cur_title = re.sub(r'\s*\(\d+\s+exercices[^)]*\)', '', m.group(1)).strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_title:
        sections.append((cur_title, '\n'.join(cur_lines)))
    return sections


def _split_md_items(section_text: str) -> list[str]:
    """Split on lines that start a new item.
    
    Handles two formats:
      Bold:  **N.  or  **N.)**   (Chapitres Démos / Courant alt / Chute / Projectile / Pendule)
      Plain: N. Source : ...      (Chapitres 4-Magnétisme, 5-Condensateur, 6-Induction)
    """
    # Bold format first
    parts = re.split(r'(?=^\*\*\d+[\.\.\)])', section_text, flags=re.MULTILINE)
    bold_items = [p.strip() for p in parts if p.strip() and re.match(r'\*\*\d+', p.strip())]
    if bold_items:
        return bold_items
    # Plain numbered format fallback (N. Source : ...)
    parts = re.split(r'(?=^\d+\.\s)', section_text, flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip() and re.match(r'^\d+\.\s', p.strip())]


def _parse_physique_block(block: str, chapter: str) -> dict | None:
    """
    Parse one physique exercise block (any of the 3 sub-formats).
    Returns normalised dict or None.
    """
    source = _backtick_source(block)
    if not source:
        # Plain format (Chapitres 4-6): "N. Source : filename.pdf (ref)"
        plain_src = re.search(r'Source\s*:\s*(\S+\.pdf)', block, re.IGNORECASE)
        if plain_src:
            source = plain_src.group(1)

    # ── Locate the énoncé ──
    # Format 1/2: *Énoncé* : text  (italic label)
    enonce_m = re.search(r'\*[ÉE]nonc[ée]\*\s*:\s*([\s\S]*)', block, re.IGNORECASE)
    if not enonce_m:
        # Format 3: **Énoncé :** or **Énoncé:**  (bold label)
        enonce_m = re.search(r'\*\*[ÉE]nonc[ée]\s*:?\*\*\s*([\s\S]*)', block, re.IGNORECASE)
    if not enonce_m:
        # Format 4 plain (Chapitres 4-6): "Énoncé : text"  (no markdown markers)
        enonce_m = re.search(r'^[ÉE]nonc[ée]\s*:\s*([\s\S]*)', block, re.IGNORECASE | re.MULTILINE)

    if enonce_m:
        enonce_raw = enonce_m.group(1).strip()
    else:
        # Fallback: everything after the source/header line
        lines = block.split('\n')
        body_lines = []
        past_header = False
        for line in lines:
            if past_header and (line.strip() and not line.strip().startswith('*Source') and not line.strip().startswith('**Source')):
                body_lines.append(line.strip())
            if 'Source' in line or 'source' in line:
                past_header = True
        enonce_raw = '\n'.join(body_lines).strip()

    # Remove trailing "next item" artefacts
    enonce_raw = re.sub(r'\n\*\*\d+[\.\)].*$', '', enonce_raw, flags=re.DOTALL).strip()
    # Remove (identique à X) notes
    enonce_raw = re.sub(r'\s*\(identique [àa]\s*\d+\)', '', enonce_raw).strip()

    if not enonce_raw or len(enonce_raw) < 15:
        return None

    intro, questions = _extract_sub_questions(enonce_raw)
    # Physique Démonstrations: a)/b) are inline in the sentence, not separate lines.
    # If no questions found, split on newline-prefixed a)/b)/1)/2) in the raw text.
    if not questions:
        inline_qs = re.findall(
            r'(?:^|\n)\s*([a-eA-E]\s*[\)\.]\s+[^\n]{5,}|[1-9]\s*[\)\.]\s+[^\n]{5,})',
            enonce_raw,
        )
        if inline_qs:
            questions = [q.strip() for q in inline_qs]
            intro = enonce_raw[:enonce_raw.find(inline_qs[0])].strip() or enonce_raw

    # Proof/demonstration exercises have no sub-questions — the whole énoncé is the task
    if not questions:
        questions = [enonce_raw]
        intro = ''

    return {
        'source':         source,
        'source_display': _source_display(source),
        'theme':          chapter,
        'chapter':        chapter,
        'subject':        'physique',
        'intro':          intro or enonce_raw,
        'enonce':         enonce_raw,
        'questions':      questions,
        'reponses':       {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT 3 — Markdown (Math)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_math_block(block: str, chapter: str) -> dict | None:
    """
    Parse one math exercise block.
    Fields: *Source* / *Thème* / *Intro* / *Questions* :
    """
    source = _backtick_source(block)

    # Thème (optional)
    theme_m = re.search(r'\*Th[eè]me\*\s*:\s*(.+)', block)
    theme = theme_m.group(1).strip() if theme_m else chapter

    # Intro
    intro_m = re.search(
        r'\*(?:Intro|[ÉE]nonc[ée])\*\s*:\s*([\s\S]+?)(?=\n\s*\*(?:Questions|Th[eè]me|Source)\*|\Z)',
        block
    )
    intro = intro_m.group(1).strip() if intro_m else ''

    # Explicit *Questions* section
    q_m = re.search(
        r'\*Questions\*\s*:\s*([\s\S]+?)(?=\n\s*\*(?:Source|Th[eè]me|Intro)\*|\n\*\*\d+[\.\)]|\Z)',
        block
    )
    if q_m:
        q_text = q_m.group(1).strip()
        questions = [l.strip() for l in q_text.split('\n') if l.strip() and len(l.strip()) > 3]
    else:
        # Questions embedded in intro
        intro_clean, questions = _extract_sub_questions(intro)
        if questions:
            intro = intro_clean

    if not intro and not questions:
        return None

    # Convert inline series data (Statistiques / Probabilités) to a pipe table
    intro = _series_to_md_table(intro)

    return {
        'source':         source,
        'source_display': _source_display(source),
        'theme':          theme,
        'chapter':        chapter,
        'subject':        'maths',
        'intro':          intro,
        'enonce':         intro + ('\n\n' + '\n'.join(questions) if questions else ''),
        'questions':      questions,
        'reponses':       {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT 4 — JSON chapitres (Chimie)
# ─────────────────────────────────────────────────────────────────────────────

def _iter_chimie_json_exos(raw: str) -> list[tuple[int, dict]]:
    """
    Iteratively extract all complete exercice JSON objects from the chimie JSON text
    using raw_decode.  Returns list of (byte_pos, obj) for every successfully-parsed
    exercice dict that has an 'enonce' key.  Skips malformed objects without aborting.
    """
    decoder = json.JSONDecoder(strict=False)
    results: list[tuple[int, dict]] = []
    # Look for potential exercice starts: {"num": N,  or  { "num":
    pos = 0
    for m in re.finditer(r'\{\s*"num"\s*:', raw):
        start = m.start()
        if start < pos:
            continue  # already consumed
        try:
            obj, abs_end = decoder.raw_decode(raw, start)
            if isinstance(obj, dict) and 'enonce' in obj:
                results.append((start, obj))
            pos = abs_end  # raw_decode returns absolute end index
        except json.JSONDecodeError:
            pos = start + 1
    return results


def _load_json_chapitres(path: Path, subject: str) -> list[dict]:
    """
    Load exo_chimie.json.  The file has two parts:
      - Chapters 1-10: JSON under {"chapitres": [...]}  (last exercice is malformed)
      - Chapters 11+:  raw markdown text embedded in a broken JSON string value.

    Strategy:
      1. Try full JSON parse; fall back to iterative raw_decode to extract exercice
         objects individually, recovering chapter info from surrounding text.
      2. Parse chapters 11+ from the markdown tail.
    """
    raw = path.read_text(encoding='utf-8')
    result = []

    # ── Part 1: JSON section (chapters 1-10) ─────────────────────────────
    try:
        data = json.JSONDecoder(strict=False).decode(raw)
        json_chapitres = data.get('chapitres', [])
        # Standard path: iterate chapitres normally
        for ch in json_chapitres:
            chapter = ch.get('titre', '').strip()
            for exo in ch.get('exercices', []):
                enonce = (exo.get('enonce') or '').strip()
                if not enonce:
                    continue
                raw_qs = exo.get('questions', [])
                questions = [
                    str(q).strip() for q in raw_qs
                    if str(q).strip() and 'voir énoncé' not in str(q).lower()
                ]
                if not questions:
                    _, questions = _extract_sub_questions(enonce)
                if not questions:
                    questions = [enonce]  # questions embedded inline in enonce
                source = exo.get('source') or ''
                result.append({
                    'source':         source,
                    'source_display': _source_display(source),
                    'theme':          chapter,
                    'chapter':        chapter,
                    'subject':        subject,
                    'intro':          enonce,
                    'enonce':         enonce,
                    'questions':      questions,
                    'reponses':       exo.get('reponses', {}),
                })
    except json.JSONDecodeError:
        # and recover their chapter from the preceding "titre" in the text
        exo_items = _iter_chimie_json_exos(raw)
        for pos, exo in exo_items:
            enonce = (exo.get('enonce') or '').strip()
            if not enonce:
                continue
            # Find the nearest chapter title preceding this exercice position
            chunk_before = raw[:pos]
            titre_m = None
            for tm in re.finditer(r'"titre"\s*:\s*"([^"]+)"', chunk_before):
                titre_m = tm  # keep last match
            chapter = titre_m.group(1).strip() if titre_m else 'Chimie'

            raw_qs = exo.get('questions', [])
            questions = [
                str(q).strip() for q in raw_qs
                if str(q).strip() and 'voir énoncé' not in str(q).lower()
            ]
            if not questions:
                _, questions = _extract_sub_questions(enonce)
            if not questions:
                questions = [enonce]  # questions embedded inline in enonce
            source = exo.get('source') or ''
            result.append({
                'source':         source,
                'source_display': _source_display(source),
                'theme':          chapter,
                'chapter':        chapter,
                'subject':        subject,
                'intro':          enonce,
                'enonce':         enonce,
                'questions':      questions,
                'reponses':       exo.get('reponses', {}),
            })

    # ── Part 2: markdown tail (chapters 11+) ───────────────────────────────
    tail_m = re.search(r'\n\s*id\s*:\s*11\b', raw)
    if tail_m:
        tail_exos = _parse_chimie_markdown_tail(raw[tail_m.start():], subject)
        result.extend(tail_exos)

    return result


def _parse_chimie_markdown_tail(text: str, subject: str) -> list[dict]:
    """
    Parse the markdown section of exo_chimie.json (chapters 11+).
    Format:
        id :N
        titre : «...»

        ### Exercice M
        **Source** : `exam_...pdf`
        **Extrait** (...) :
        > questions / text

        **Réponses** :
        text
    """
    result = []

    # Split into chapter blocks by 'id :N'
    chapter_blocks = re.split(r'\n\s*id\s*:\s*\d+', text)

    for block in chapter_blocks:
        if not block.strip():
            continue

        # Extract chapter title
        titre_m = re.search(r'titre\s*:\s*[«»""]?(.+?)[«»""]?\s*\n', block)
        chapter = titre_m.group(1).strip().strip('«» "') if titre_m else 'Chimie organique'

        # Split into exercice sub-blocks
        exo_blocks = re.split(r'###\s+Exercice\s+\d+', block)

        for exo_block in exo_blocks[1:]:  # skip pre-exercice text
            source_m = re.search(r'\*\*Source\*\*\s*:\s*`(exam_[^`]+\.pdf)`', exo_block)
            source = source_m.group(1) if source_m else ''

            # Extrait block
            extrait_m = re.search(
                r'\*\*Extrait\*\*[^:]*:\s*([\s\S]+?)(?=\*\*R[ée]ponses?\*\*|$)',
                exo_block, re.IGNORECASE
            )
            if extrait_m:
                extrait = extrait_m.group(1).strip()
                # Clean blockquote markers
                extrait = re.sub(r'^>\s*', '', extrait, flags=re.MULTILINE)
                extrait = extrait.strip()
            else:
                extrait = ''

            # Réponses block (optional)
            rep_m = re.search(
                r'\*\*R[ée]ponses?\*\*\s*:\s*([\s\S]+?)(?=---|\Z)', exo_block, re.IGNORECASE
            )
            reponse = rep_m.group(1).strip() if rep_m else ''

            if not extrait or len(extrait) < 10:
                continue

            intro, questions = _extract_sub_questions(extrait)
            if not questions:
                questions = [extrait]  # fill-in-the-blank or single-sentence question

            result.append({
                'source':         source,
                'source_display': _source_display(source),
                'theme':          chapter,
                'chapter':        chapter,
                'subject':        subject,
                'intro':          intro or extrait,
                'enonce':         extrait,
                'questions':      questions,
                'reponses':       {'réponse': reponse} if reponse else {},
            })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# MARKDOWN LOADER (shared by physique + math)
# ─────────────────────────────────────────────────────────────────────────────

def _load_markdown(path: Path, subject: str, parser_fn) -> list[dict]:
    text = path.read_text(encoding='utf-8')
    sections = _split_md_sections(text)
    result = []
    for chapter, section_text in sections:
        items = _split_md_items(section_text)
        for item in items:
            parsed = parser_fn(item, chapter)
            if parsed:
                # CLEAN corruptions and VALIDATE before adding
                parsed = _sanitize_exercise(parsed)
                if _validate_exercise(parsed):
                    result.append(parsed)
                else:
                    # Skip corrupted/incomplete exercise
                    pass
    return result


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def get_all_exercises(subject: str) -> list[dict]:
    """Load and cache all exercises for a subject from exo*.json."""
    global _cache
    if subject in _cache:
        return _cache[subject]

    entry = _EXO_FILES.get(subject)
    if not entry:
        return []

    fname, fmt = entry
    path = _DB_DIR / fname
    if not path.exists() or path.stat().st_size < 10:
        return []

    try:
        if fmt == 'json_list':
            result = _load_json_list(path, subject)
        elif fmt == 'markdown_physique':
            result = _load_markdown(path, subject, _parse_physique_block)
        elif fmt == 'markdown_math':
            result = _load_markdown(path, subject, _parse_math_block)
        elif fmt == 'json_chapitres':
            result = _load_json_chapitres(path, subject)
        else:
            result = []
    except Exception as e:
        print(f"[exo_loader] Failed to load {fname}: {e}")
        result = []

    _cache[subject] = result
    print(f"[exo_loader] Loaded {len(result)} exercises for {subject} from {fname}")
    return result


def get_exercises(subject: str, chapter: str = '', n: int = 10) -> list[dict]:
    """
    Return up to n real exercises for the subject, optionally filtered by chapter.
    When chapter is given, keyword-match against the exercise's chapter/theme fields.
    """
    pool = get_all_exercises(subject)
    if not pool:
        return []

    if chapter and chapter.lower().strip() not in ('aléatoire', 'random', ''):
        ch_lower = chapter.lower().strip()

        # Generic words that appear in every chapter title → useless for discrimination
        _STOP = {'chapitre', 'chapter', 'exercice', 'exercices', 'sujet', 'partie',
                 'les', 'des', 'une', 'the', 'and', 'par', 'sur'}

        def _matches(exo: dict) -> bool:
            # Match ONLY against the chapter field (not theme) to avoid cross-chapter
            # contamination when themes contain keywords from sibling chapters
            # (e.g. theme "Statistiques et probabilités" in a Probabilités chapter)
            ch_field = exo.get('chapter', '').lower()
            # Priority 1: exact chapter title match
            if ch_lower == ch_field:
                return True
            if ch_lower in ch_field or ch_field in ch_lower:
                return True
            # Priority 2: ALL distinctive keywords (no stop words) must be in chapter field
            kws = [w for w in re.findall(r'\b[a-zA-ZÀ-ÿ]{3,}\b', ch_lower) if w not in _STOP]
            if kws:
                return all(kw in ch_field for kw in kws)
            return False

        filtered = [e for e in pool if _matches(e)]
        if filtered:
            pool = filtered
        # else: chapter not found → return from full pool (better than nothing)

        # ── Content-based exclusion for physique: prevent cross-chapter contamination ──
        # Some exercises end up in the wrong chapter section in the raw JSON because they
        # appeared in the same exam PDF as exercises from that chapter.
        _PHYSIQUE_EXCLUSIONS = {
            'magnétisme': ['sinusoïdal', 'sinusoidal', 'impédance', 'impedance',
                           'résonance', 'resonance', 'circuit rlc', '(r l c)', '(r-l-c)',
                           'fléchette', 'pistolet', 'ressort', 'condensateur de capacité variable'],
            'condensateur': ['sinusoïdal', 'sinusoidal', 'impédance', 'impedance',
                             'résonance', 'resonance', 'circuit rlc', '(r l c)',
                             'fléchette', 'pistolet'],
            'induction électromagnétique': ['sinusoïdal', 'sinusoidal', 'impédance',
                                            'fléchette', 'pistolet', 'ressort'],
            'chute libre': ['condensateur', 'solénoïde', 'inductance', 'sinusoïdal'],
            'projectile': ['condensateur', 'solénoïde', 'inductance', 'sinusoïdal'],
            'pendule': ['condensateur', 'solénoïde', 'inductance', 'sinusoïdal', 'impédance'],
        }
        for _kw, _excl in _PHYSIQUE_EXCLUSIONS.items():
            if _kw in ch_lower:
                content_filtered = [
                    e for e in pool
                    if not any(
                        bad in (e.get('intro', '') + ' ' + ' '.join(e.get('questions', []))).lower()
                        for bad in _excl
                    )
                ]
                if content_filtered:
                    pool = content_filtered
                break

    shuffled = list(pool)
    random.shuffle(shuffled)
    return shuffled[:n]


def get_random_exercise(subject: str, chapter: str = '') -> dict | None:
    """Return one random exercise (or None if nothing found)."""
    exos = get_exercises(subject, chapter, n=1)
    return exos[0] if exos else None


def get_chapters(subject: str) -> list[dict]:
    """Return distinct chapters from the real exo file for subject, in discovery order."""
    exos = get_all_exercises(subject)
    seen_order: list[str] = []
    seen_set: set[str] = set()
    for exo in exos:
        ch = (exo.get('chapter') or exo.get('theme') or '').strip()
        if ch and ch not in seen_set:
            seen_set.add(ch)
            seen_order.append(ch)
    return [{'id': i + 1, 'title': ch, 'num': i + 1} for i, ch in enumerate(seen_order)]


def available_subjects() -> list[str]:
    """Return subjects that have non-empty exo files."""
    return list(_EXO_FILES.keys())
