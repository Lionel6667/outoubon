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


def _dedupe_obvious_repeats(text: str) -> str:
    """Supprime les répétitions évidentes (artefacts OCR/parsing)."""
    if not text:
        return text
    text = _DUP_TOKEN_RUN.sub(r'\1', text)
    # Phrases dupliquées côte à côte
    words = text.split()
    if len(words) < 6:
        return text
    out: list[str] = []
    i = 0
    while i < len(words):
        out.append(words[i])
        # Skip immediate duplicate word
        if i + 1 < len(words) and words[i + 1] == words[i]:
            i += 1
        i += 1
    return ' '.join(out)


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


def format_exercise_display_local(subject: str, intro: str, questions: list) -> dict:
    """
    Nettoie l'affichage d'un exercice sans appel API.
    - Tableaux markdown via _global_format_tables
    - Déduplication légère
    - Enveloppement LaTeX inline basique
    """
    from core.gemini import _global_format_tables  # import paresseux (évite cycle au load)
    from core.exo_loader import _extract_sub_questions, _series_to_md_table

    intro = (intro or '').strip()
    questions = [str(q).strip() for q in (questions or []) if str(q).strip()]
    intro_clean, extracted = _extract_sub_questions(intro)
    if extracted:
        if intro_clean:
            intro = intro_clean
        if not questions or len(extracted) > len(questions):
            questions = extracted

    intro = _dedupe_obvious_repeats(intro)
    intro = _series_to_md_table(intro)
    intro = _global_format_tables(intro)
    intro = _normalize_math_delims(intro)
    intro = _plain_urn_labels(intro)
    intro = _wrap_inline_math(intro)
    intro = _fix_decimal_commas_in_math(intro)

    cleaned_qs: list[str] = []
    for q in questions:
        q = _dedupe_obvious_repeats(q)
        q = _global_format_tables(q)
        q = _normalize_math_delims(q)
        q = _plain_urn_labels(q)
        q = _wrap_inline_math(q)
        q = _fix_decimal_commas_in_math(q)
        cleaned_qs.append(q)

    return {'intro': intro, 'questions': cleaned_qs or questions}
