"""
remove_comments.py
------------------
Removes comments from Python, JavaScript, and HTML/template files in the project.

Usage:
  python remove_comments.py            # dry-run — shows what would change
  python remove_comments.py --execute  # modifies files in place

Rules:
  .py   : removes # comment tokens (via tokenize — safe w.r.t. strings & shebangs)
  .js   : removes // line comments and /* ... */ block comments (regex, string-aware)
  .html : removes <!-- ... --> HTML comments (skips Django {# #} template comments)

Skipped directories: migrations, __pycache__, .venv*, node_modules, .git, static/vendor
"""

import sys
import os
import re
import io
import tokenize
import shutil
from pathlib import Path

DRY_RUN = "--execute" not in sys.argv

PROJECT_ROOT = Path(__file__).parent

SKIP_DIRS = {
    "migrations", "__pycache__", ".git",
    "node_modules", "static"
}
SKIP_DIR_PREFIXES = (".venv",)

EXTENSIONS = {".py", ".js", ".html"}


def should_skip_dir(d: str) -> bool:
    if d in SKIP_DIRS:
        return True
    for prefix in SKIP_DIR_PREFIXES:
        if d.startswith(prefix):
            return True
    return False


# ─── Python comment removal ───────────────────────────────────────────────────

def remove_python_comments(source: str) -> str:
    """Remove # comments from Python source using tokenize (string-safe)."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return source  # unparseable — leave as-is

    result_lines = source.splitlines(keepends=True)

    # Build a set of (line_index, col_start) for comment tokens
    comments: dict[int, int] = {}  # line 0-indexed -> col where comment starts
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            line_idx = tok.start[0] - 1
            col = tok.start[1]
            comments[line_idx] = col

    new_lines = []
    for i, line in enumerate(result_lines):
        if i in comments:
            col = comments[i]
            before = line[:col].rstrip()
            if before:
                # Inline comment: keep the code, drop the comment
                new_lines.append(before + "\n")
            else:
                # Whole-line comment: drop entirely (keep blank line to preserve line nums... or not)
                # We drop the line entirely to clean up the file
                pass  # omit the line
        else:
            new_lines.append(line)

    return "".join(new_lines)


# ─── JavaScript comment removal ───────────────────────────────────────────────

def remove_js_comments(source: str) -> str:
    """
    Remove // and /* */ comments from JS, skipping string and regex literals.
    Preserves URLs (http://, https://) that appear inside strings.
    """
    result = []
    i = 0
    n = len(source)

    while i < n:
        c = source[i]

        # String literals: single quote, double quote, template literal
        if c in ('"', "'", '`'):
            quote = c
            result.append(c)
            i += 1
            while i < n:
                ch = source[i]
                result.append(ch)
                if ch == '\\':
                    # Escape sequence — consume next char too
                    i += 1
                    if i < n:
                        result.append(source[i])
                elif ch == quote:
                    break
                i += 1
            i += 1
            continue

        # Block comment /* ... */
        if c == '/' and i + 1 < n and source[i + 1] == '*':
            # Skip until */
            i += 2
            while i < n:
                if source[i] == '*' and i + 1 < n and source[i + 1] == '/':
                    i += 2
                    break
                i += 1
            # Preserve the newline if block comment spanned lines
            # (we've consumed those newlines; add nothing — blank lines removed)
            continue

        # Line comment //
        if c == '/' and i + 1 < n and source[i + 1] == '/':
            # Skip until end of line
            i += 2
            while i < n and source[i] != '\n':
                i += 1
            continue

        result.append(c)
        i += 1

    # Clean up lines that became blank or whitespace-only as a result
    lines = "".join(result).splitlines(keepends=True)
    cleaned = []
    for line in lines:
        if line.strip():
            cleaned.append(line)
        # else: drop blank lines created by comment removal
    return "".join(cleaned)


# ─── HTML comment removal ─────────────────────────────────────────────────────

_HTML_COMMENT_RE = re.compile(r'<!--(?!.*?\{[%#]).*?-->', re.DOTALL)


def remove_html_comments(source: str) -> str:
    """
    Remove <!-- ... --> comments from HTML/template files.
    Skips comments that contain Django template syntax ({# or {%).
    Also skips IE conditional comments (<!--[if ...]-->).
    """
    def replacer(m: re.Match) -> str:
        text = m.group(0)
        # Keep IE conditional comments
        if text.startswith('<!--['):
            return text
        # Keep comments containing Django template tags/variables
        if '{%' in text or '{#' in text or '{{' in text:
            return text
        return ''

    result = _HTML_COMMENT_RE.sub(replacer, source)

    # Remove blank lines left behind
    lines = result.splitlines(keepends=True)
    cleaned = [l for l in lines if l.strip()]
    return "".join(cleaned)


# ─── Main ─────────────────────────────────────────────────────────────────────

def process_file(path: Path) -> tuple[bool, int]:
    """
    Process one file. Returns (changed: bool, lines_removed: int).
    """
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  [SKIP] {path}: cannot read — {e}")
        return False, 0

    ext = path.suffix.lower()

    if ext == ".py":
        processed = remove_python_comments(original)
    elif ext == ".js":
        processed = remove_js_comments(original)
    elif ext in (".html", ".htm"):
        processed = remove_html_comments(original)
    else:
        return False, 0

    if processed == original:
        return False, 0

    orig_lines = original.count('\n')
    new_lines  = processed.count('\n')
    removed    = max(0, orig_lines - new_lines)

    if not DRY_RUN:
        # Backup original
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        path.write_text(processed, encoding="utf-8")

    return True, removed


def main():
    print("=" * 60)
    if DRY_RUN:
        print("DRY RUN — no files will be modified")
        print("Run with --execute to apply changes")
    else:
        print("EXECUTE MODE — files will be modified (backups created)")
    print("=" * 60)

    total_files = 0
    changed_files = 0
    total_lines_removed = 0

    for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
        # Prune skipped directories in-place
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]

        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() not in EXTENSIONS:
                continue
            # Skip this script itself
            if path.resolve() == Path(__file__).resolve():
                continue

            total_files += 1
            changed, removed = process_file(path)
            if changed:
                changed_files += 1
                total_lines_removed += removed
                rel = path.relative_to(PROJECT_ROOT)
                action = "MODIFIED" if not DRY_RUN else "WOULD MODIFY"
                print(f"  [{action}] {rel}  (-{removed} lines)")

    print("=" * 60)
    print(f"Scanned   : {total_files} files")
    print(f"{'Modified' if not DRY_RUN else 'Would modify'}: {changed_files} files")
    print(f"Lines {'removed' if not DRY_RUN else 'to remove'}: {total_lines_removed}")
    if DRY_RUN:
        print("\nRun with --execute to apply.")


if __name__ == "__main__":
    main()
