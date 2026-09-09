"""
Formatage local des exercices — zéro appel IA au runtime.
Remplace l'ancien format_exercise_display basé sur Groq/DeepSeek.
"""
from __future__ import annotations

import re


_MATH_INLINE_PAT = re.compile(
    r'(?<!\$)'
    r'('
    r'E\([A-Z]\)|Var\([A-Z]\)|P\([A-Z]\s*[∈=]\s*[^)]+\)|'
    r'[A-Za-z]_\{[^}]+\}|[A-Za-z]_\d+|'
    r'\\(?:frac|sqrt|sum|int|lim|alpha|beta|gamma|delta|theta|pi)\b[^$\s]*'
    r')'
    r'(?!\$)'
)

_DUP_TOKEN_RUN = re.compile(r'(\S+(?:\s+\S+){2,}?)(?:\s+\1)+')


def _dedupe_words_in_line(line: str) -> str:
    """Déduplique les mots d'une ligne sans toucher aux cellules `|` d'un tableau."""
    if '|' in line:
        return line
    words = line.split()
    if len(words) < 6:
        return line
    out: list[str] = []
    i = 0
    while i < len(words):
        out.append(words[i])
        if i + 1 < len(words) and words[i + 1] == words[i] and words[i] != '|':
            i += 1
        i += 1
    leading = line[: len(line) - len(line.lstrip())] if line.strip() else ''
    return leading + ' '.join(out)


def _dedupe_obvious_repeats(text: str) -> str:
    """Supprime les répétitions évidentes sans écraser les tableaux markdown."""
    if not text:
        return text
    # Traiter ligne par ligne : split() sur tout le texte collait les
    # lignes d'un tableau (`| x |` + `| y |`) en une seule rangée.
    parts = text.split('\n')
    cleaned = []
    for part in parts:
        stripped = part.lstrip()
        if '|' in part or stripped.startswith('<') or 'tbl-wrap' in part:
            cleaned.append(part)
            continue
        part = _DUP_TOKEN_RUN.sub(r'\1', part)
        cleaned.append(_dedupe_words_in_line(part))
    return '\n'.join(cleaned)


def _normalize_math_delims(text: str) -> str:
    """Convertit \\(...\\) et \\[...\\] en délimiteurs $ pour le rendu client."""
    if not text:
        return text
    text = re.sub(r'\\\((.+?)\\\)', lambda m: '$' + m.group(1) + '$', text)
    text = re.sub(r'\\\[(.+?)\\\]', lambda m: '$$' + m.group(1) + '$$', text)
    return text


def _fix_decimal_commas_in_math(text: str) -> str:
    """0,12 dans $...$ → 0.12 (MathJax refuse la virgule décimale).

    ATTENTION : ne jamais toucher aux virgules qui séparent une liste de
    nombres (ex : ensemble \\{1,2,3,4\\} ou liste x=25,40,42) — ce sont des
    séparateurs, pas des décimales. Sinon l'énoncé devient faux.
    """
    if not text or '$' not in text:
        return text

    def _fix_block(m: re.Match) -> str:
        inner = m.group(1)
        # Ensembles / arguments entre accolades : virgule = séparateur → intact.
        if '{' in inner or '}' in inner:
            return '$' + inner + '$'
        # Liste de 3 nombres ou plus séparés par des virgules → séparateur → intact.
        if re.search(r'\d\s*,\s*\d+\s*,\s*\d', inner):
            return '$' + inner + '$'
        # Décimale française isolée : 0,12 → 0.12
        inner = re.sub(r'(?<=\d),(?=\d)', '.', inner)
        return '$' + inner + '$'

    return re.sub(r'\$([^$]+)\$', _fix_block, text)


def _plain_urn_labels(text: str) -> str:
    """U_1 / \\(U_2\\) dans l'énoncé → U1 (labels d'urnes, pas de LaTeX)."""
    if not text:
        return text
    text = re.sub(r'\\?\(U_(\d+)\\?\)', r'U\1', text)
    text = re.sub(r'\$U_(\d+)\$', r'U\1', text)
    return text


def _wrap_inline_math(text: str) -> str:
    """Enveloppe les expressions mathématiques courantes dans $...$ si pas déjà fait."""
    if not text or '$' in text:
        return text

    def _wrap(m: re.Match) -> str:
        expr = m.group(1).strip()
        if not expr:
            return m.group(0)
        return f'${expr}$'

    return _MATH_INLINE_PAT.sub(_wrap, text)


_SEP_CELL = re.compile(r'^:?-{2,}:?$')


def _cell_html(value: str) -> str:
    return (
        (value or '')
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def _split_pipe_cells(line: str) -> list[str] | None:
    """Découpe une ligne de tableau GFM, avec ou sans pipes extérieurs."""
    s = (line or '').strip()
    if '|' not in s:
        return None
    cells = [c.strip() for c in s.strip('|').split('|')]
    if len(cells) < 2:
        return None
    return cells


def _is_sep_cells(cells: list[str] | None) -> bool:
    if not cells:
        return False
    nonempty = [c.replace(' ', '') for c in cells if c.strip()]
    return bool(nonempty) and all(_SEP_CELL.match(c) for c in nonempty)


def _looks_like_md_table(text: str) -> bool:
    """True si le texte contient déjà un bloc tabulaire (pipes, avec ou sans ---)."""
    if not text or '|' not in text:
        return False
    if re.search(r'\|.+\|\s*\n\s*\|[-:| ]+\|', text):
        return True
    prev = False
    for ln in text.split('\n'):
        cells = _split_pipe_cells(ln)
        is_row = cells is not None
        if is_row and prev:
            return True
        prev = is_row
    return False


def _restore_collapsed_md_table(text: str) -> str:
    """Remet les retours à la ligne d'un tableau markdown collé sur une ligne."""
    if not text or '|' not in text:
        return text
    if re.search(r'\n\s*\|', text):
        return text
    text = re.sub(r'(\|)\s+(\|(?:[\s]*:?-{3,}:?[\s]*\|)+)', r'\1\n\2', text)
    text = re.sub(r'(\|)\s+(\|\s*[A-Za-zÀ-ÿ_$])', r'\1\n\2', text)
    return text


def _normalize_loose_pipe_tables(text: str) -> str:
    """GFM lâche (`Année | C | Yd`) → `| Année | C | Yd |`."""
    if not text or '|' not in text or '<table' in text.lower():
        return text
    lines = text.split('\n')
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        cells = _split_pipe_cells(lines[i])
        if cells is None:
            out.append(lines[i])
            i += 1
            continue
        run = [cells]
        j = i + 1
        while j < n:
            nxt = _split_pipe_cells(lines[j])
            if nxt is None:
                break
            run.append(nxt)
            j += 1
        data_rows = [r for r in run if not _is_sep_cells(r)]
        if len(data_rows) >= 2:
            width = max(len(r) for r in run)
            for row in run:
                padded = row + [''] * (width - len(row))
                if _is_sep_cells(row):
                    seps = [(c.replace(' ', '') or '---') for c in padded]
                    out.append('| ' + ' | '.join(seps) + ' |')
                else:
                    out.append('| ' + ' | '.join(padded) + ' |')
            i = j
            continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


def _tsv_blocks_to_md(text: str) -> str:
    """Blocs TSV (2+ lignes, 2+ colonnes) → markdown."""
    if not text or '\t' not in text or '<table' in text.lower():
        return text
    lines = text.split('\n')
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        cols = lines[i].split('\t')
        if len(cols) >= 2 and '\t' in lines[i]:
            run = [cols]
            j = i + 1
            while j < n and '\t' in lines[j] and len(lines[j].split('\t')) >= 2:
                run.append(lines[j].split('\t'))
                j += 1
            if len(run) >= 2:
                width = max(len(r) for r in run)
                for row in run:
                    padded = [c.strip() for c in row] + [''] * (width - len(row))
                    out.append('| ' + ' | '.join(padded) + ' |')
                i = j
                continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


def _rows_to_html(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [''] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    th = ''.join(f'<th>{_cell_html(c)}</th>' for c in head)
    trs = ''.join(
        '<tr>' + ''.join(f'<td>{_cell_html(c)}</td>' for c in r) + '</tr>'
        for r in body
    )
    return (
        f'<div class="tbl-wrap"><table><thead><tr>{th}</tr></thead>'
        f'<tbody>{trs}</tbody></table></div>'
    )


def _md_tables_to_html(text: str) -> str:
    """Convertit les tableaux markdown (2+ lignes `| ... |`) en HTML."""
    if not text or '|' not in text or '<table' in text.lower():
        return text

    def _convert_block(block: str) -> str:
        lines = [ln.strip() for ln in block.strip().split('\n') if ln.strip()]
        rows: list[list[str]] = []
        for ln in lines:
            cells = _split_pipe_cells(ln)
            if cells is None:
                return block
            if _is_sep_cells(cells) or re.match(r'^\|[-:| ]+\|$', ln):
                continue
            rows.append(cells)
        if len(rows) < 2:
            return block
        return _rows_to_html(rows)

    padded = text if text.endswith('\n') else text + '\n'
    return re.sub(
        r'(?:^[ \t]*\|.+\|[ \t]*\n){1,}(?:^[ \t]*\|.+\|[ \t]*)',
        lambda m: _convert_block(m.group(0)) + '\n',
        padded,
        flags=re.MULTILINE,
    )


def _points_tuples_to_table(text: str) -> str:
    """(3,3), (5,5), (6,11) ou \\((1,1), (3,2)\\) → tableau x/y."""
    if not text or _looks_like_md_table(text) or '<table' in text.lower():
        return text
    matches = list(re.finditer(r'\(\s*(-?\d+(?:[.,]\d+)?)\s*,\s*(-?\d+(?:[.,]\d+)?)\s*\)', text))
    if len(matches) < 2:
        return text
    xs = [m.group(1) for m in matches]
    ys = [m.group(2) for m in matches]
    n = len(xs)
    header = '| | ' + ' | '.join(str(i) for i in range(1, n + 1)) + ' |'
    sep = '|---|' + '---|' * n
    row_x = '| x | ' + ' | '.join(xs) + ' |'
    row_y = '| y | ' + ' | '.join(ys) + ' |'
    table = '\n'.join([header, sep, row_x, row_y])
    start, end = matches[0].start(), matches[-1].end()
    if start >= 2 and text[start - 2:start] == '\\(':
        start -= 2
    if text[end:end + 2] == '\\)':
        end += 2
    before = text[:start].rstrip(' :')
    after = text[end:].lstrip(' .;')
    return (before + '\n\n' + table + ('\n\n' + after if after else '')).strip()


def _classes_effectifs_to_table(text: str) -> str:
    """classes [4;8[, [8;12[ ; effectifs 8,14 → tableau."""
    if not text or _looks_like_md_table(text) or '<table' in text.lower():
        return text
    m = re.search(
        r'(?is)classes?\s*((?:\[[^\[\]]+[\]\[]\s*,?\s*)+)\s*;\s*effectifs?\s*([-\d][-\d,;.\s]*)',
        text,
    )
    if not m:
        return text
    classes = re.findall(r'\[[^\[\]]+[\[\]]', m.group(1))
    from core.exo_loader import _split_series_values
    effectifs = _split_series_values(m.group(2).strip(' .;'), expected_n=len(classes))
    if len(classes) < 2 or len(classes) != len(effectifs):
        return text
    header = '| Classe | ' + ' | '.join(classes) + ' |'
    sep = '|---|' + '---|' * len(classes)
    row = '| Effectif | ' + ' | '.join(effectifs) + ' |'
    table = '\n'.join([header, sep, row])
    return text[:m.start()].rstrip() + '\n\n' + table + text[m.end():]


def _plain_xy_to_table(text: str) -> str:
    """heures x = 2,2,6 ; notes y = 5,10  (sans délimiteurs LaTeX)."""
    if not text or _looks_like_md_table(text) or '<table' in text.lower():
        return text
    m = re.search(
        r'(?is)(?:^|[^\w])x\s*=\s*([-\d.][-\d,;.\s]*?)\s*;\s*(?:[A-Za-zÀ-ÿ][^\n]{0,35}?)?y\s*=\s*([-\d][-\d,;.\s]+?)(?=\s*[.!?]|\s*$)',
        text,
    )
    if not m:
        m = re.search(
            r'(?is)(?:^|[^\w])x\s*=\s*([-\d.][-\d,;.\s]*?)\s*(?:et\s+|,)\s*y\s*=\s*([-\d][-\d,;.\s]+?)(?=\s*[.!?]|\s*$)',
            text,
        )
    if not m:
        return text
    from core.exo_loader import _split_series_values
    xs = _split_series_values(m.group(1).strip(' .;'))
    ys = _split_series_values(m.group(2).strip(' .;'), expected_n=len(xs))
    if len(xs) < 2 or len(xs) != len(ys):
        return text
    n = len(xs)
    header = '| | ' + ' | '.join(str(i) for i in range(1, n + 1)) + ' |'
    sep = '|---|' + '---|' * n
    table = '\n'.join([
        header, sep,
        '| x | ' + ' | '.join(xs) + ' |',
        '| y | ' + ' | '.join(ys) + ' |',
    ])
    return text[:m.start()].rstrip() + '\n\n' + table + text[m.end():]


def _labeled_pair_to_table(text: str) -> str:
    """année/population, Machine X/Y — deux listes nommées de même longueur."""
    if not text or _looks_like_md_table(text) or '<table' in text.lower():
        return text
    from core.exo_loader import _split_series_values
    m = re.search(
        r'(?is)(ann[ée]es?)\s+([-\d][-\d,;.\s]+?)\s*;\s*(population[^\d\n]{0,40})\s*([-\d][-\d,;.\s]+)',
        text,
    )
    if m:
        lab1, s1, lab2, s2 = m.group(1).strip(), m.group(2), m.group(3).strip(), m.group(4)
    else:
        m = re.search(
            r'(?is)machine\s*x\s*:\s*([-\d][-\d,;.\s]+?)\s*machine\s*y\s*:\s*([-\d][-\d,;.\s]+)',
            text,
        )
        if not m:
            return text
        lab1, s1, lab2, s2 = 'X', m.group(1), 'Y', m.group(2)
    xs = _split_series_values(s1.strip(' .;'))
    ys = _split_series_values(s2.strip(' .;'), expected_n=len(xs))
    if len(xs) < 2 or len(xs) != len(ys):
        return text
    n = len(xs)
    header = '| | ' + ' | '.join(str(i) for i in range(1, n + 1)) + ' |'
    sep = '|---|' + '---|' * n
    table = '\n'.join([
        header, sep,
        f'| {lab1} | ' + ' | '.join(xs) + ' |',
        f'| {lab2} | ' + ' | '.join(ys) + ' |',
    ])
    return text[:m.start()].rstrip() + '\n\n' + table + text[m.end():]


def _colonne_ab_to_table(text: str) -> str:
    """Colonne A / Colonne B (ou Column A/B) → tableau à 2 colonnes."""
    if not text or _looks_like_md_table(text) or '<table' in text.lower():
        return text
    m = re.search(
        r'(?is)(?:Colonne\s*A|Column\s*A)\s*\n(.*?)\n\s*(?:Colonne\s*B|Column\s*B)\s*\n(.*?)(?=\n\n|\Z)',
        text,
    )
    if not m:
        return text
    left_items = [l.strip() for l in m.group(1).splitlines() if l.strip()]
    right_items = [r.strip() for r in m.group(2).splitlines() if r.strip()]
    if len(left_items) < 2 and len(right_items) < 2:
        return text
    n = max(len(left_items), len(right_items))
    left_items += [''] * (n - len(left_items))
    right_items += [''] * (n - len(right_items))
    rows = [['Colonne A', 'Colonne B']] + [
        [l, r] for l, r in zip(left_items, right_items)
    ]
    table = '\n'.join('| ' + ' | '.join(r) + ' |' for r in rows)
    return text[:m.start()].rstrip() + '\n\n' + table + '\n' + text[m.end():]


def _valeurs_effectifs_to_table(text: str) -> str:
    """valeurs 1,2,3 ; effectifs 4,5,6  (avec ou sans « avec »)."""
    if not text or _looks_like_md_table(text) or '<table' in text.lower():
        return text
    m = re.search(
        r'(?is)valeurs?\s+(?:[xX]\s*=\s*)?([-\d][-\d,;.\s]*?)\s*(?:;|,)?\s*(?:avec\s+)?effectifs?\s+([-\d][-\d,;.\s]+)',
        text,
    )
    if not m:
        return text
    from core.exo_loader import _split_series_values
    vals = _split_series_values(m.group(1).strip(' .;'))
    effectifs = _split_series_values(m.group(2).strip(' .;'), expected_n=len(vals))
    if len(vals) < 2 or len(vals) != len(effectifs):
        return text
    header = '| Valeurs | ' + ' | '.join(vals) + ' |'
    sep = '|---|' + '---|' * len(vals)
    row = '| Effectifs | ' + ' | '.join(effectifs) + ' |'
    table = '\n'.join([header, sep, row])
    return text[:m.start()].rstrip() + '\n\n' + table + text[m.end():]


def _unify_series_delims(text: str) -> str:
    """$x=1,2,3$ → \\(x=1,2,3\\) pour réutiliser le convertisseur de séries."""
    if not text or '$' not in text:
        return text

    def _repl(m: re.Match) -> str:
        vals = m.group(2)
        if ',' not in vals and ';' not in vals:
            return m.group(0)
        return '\\(' + m.group(1) + '=' + vals + '\\)'

    return re.sub(r'\$([A-Za-z_]\w*)\s*=\s*([^$]+)\$', _repl, text)


def _xy_lists_to_md(xs: list[str], ys: list[str]) -> str:
    if len(xs) < 2 or len(xs) != len(ys):
        return ''
    n = len(xs)
    header = '| | ' + ' | '.join(str(i) for i in range(1, n + 1)) + ' |'
    sep = '|---|' + '---|' * n
    return '\n'.join([
        header, sep,
        '| x | ' + ' | '.join(xs) + ' |',
        '| y | ' + ' | '.join(ys) + ' |',
    ])


def _tabularize(text: str) -> str:
    """Tous les formats tabulaires → markdown puis HTML."""
    if not text:
        return text
    if '<table' in text.lower():
        return text
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('│', '|')
    text = _restore_collapsed_md_table(text)
    text = _normalize_loose_pipe_tables(text)
    text = _tsv_blocks_to_md(text)
    if not _looks_like_md_table(text):
        from core.gemini import _global_format_tables
        from core.exo_loader import _series_to_md_table
        text = _unify_series_delims(text)
        text = _series_to_md_table(text)
        if not _looks_like_md_table(text):
            text = _points_tuples_to_table(text)
        if not _looks_like_md_table(text):
            text = _classes_effectifs_to_table(text)
        if not _looks_like_md_table(text):
            text = _valeurs_effectifs_to_table(text)
        if not _looks_like_md_table(text):
            text = _plain_xy_to_table(text)
        if not _looks_like_md_table(text):
            text = _labeled_pair_to_table(text)
        if not _looks_like_md_table(text):
            text = _colonne_ab_to_table(text)
        if not _looks_like_md_table(text):
            text = _global_format_tables(text)
            def _xy_ws(m):
                xs = [v for v in re.split(r'[ \t]+', m.group(1).strip()) if v]
                ys = [v for v in re.split(r'[ \t]+', m.group(2).strip()) if v]
                md = _xy_lists_to_md(xs, ys)
                return '\n' + md + '\n' if md else m.group(0)
            text = re.sub(
                r'(?im)^x[ \t]+([\d., \t]+)\s*\n[ \t]*y[ \t]+([\d., \t]+)',
                _xy_ws,
                text,
            )
        text = _normalize_loose_pipe_tables(text)
    return _md_tables_to_html(text)


def format_exercise_display_local(subject: str, intro: str, questions: list) -> dict:
    """
    Nettoie l'affichage d'un exercice sans appel API.
    - Tableaux markdown via _global_format_tables
    - Déduplication légère
    - Enveloppement LaTeX inline basique
    """
    from core.exo_loader import _extract_sub_questions

    intro = (intro or '').strip()
    questions = [str(q).strip() for q in (questions or []) if str(q).strip()]
    intro_clean, extracted = _extract_sub_questions(intro)
    if extracted:
        if intro_clean:
            intro = intro_clean
        if not questions or len(extracted) > len(questions):
            questions = extracted

    intro = _dedupe_obvious_repeats(intro)
    intro = _tabularize(intro).strip()
    intro = _normalize_math_delims(intro)
    intro = _plain_urn_labels(intro)
    intro = _wrap_inline_math(intro)
    intro = _fix_decimal_commas_in_math(intro)

    cleaned_qs: list[str] = []
    for q in questions:
        q = _dedupe_obvious_repeats(q)
        q = _tabularize(q).strip()
        q = _normalize_math_delims(q)
        q = _plain_urn_labels(q)
        q = _wrap_inline_math(q)
        q = _fix_decimal_commas_in_math(q)
        cleaned_qs.append(q)

    return {'intro': intro, 'questions': cleaned_qs or questions}
