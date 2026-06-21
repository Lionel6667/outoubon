"""
Extraction des réponses élève depuis la mise au net (examen blanc).
"""

import re
from typing import Optional


def _normalize(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '').strip().lower())


def extract_answer_for_question(
    mise_au_net: str,
    question_num: int,
    section_label: str = '',
    question_text: str = '',
) -> str:
    """
    Cherche la réponse de l'élève pour une question donnée dans le texte libre.
    Patterns : "Exercice 2", "2.", "2-", "2:", "Q2", "— Exercice 2 —"
    """
    if not mise_au_net or not mise_au_net.strip():
        return ''

    text = mise_au_net.replace('\r\n', '\n')
    n = int(question_num or 0)
    if n <= 0:
        return ''

    patterns = [
        rf'(?:^|\n)\s*(?:exercice|exo|question|q)\s*{n}\s*[:\.\-\)]\s*([^\n]+(?:\n(?!\s*(?:exercice|exo|question|q)\s*\d)[^\n]+)*)',
        rf'(?:^|\n)\s*{n}\s*[\.\)\:\-]\s*([^\n]+(?:\n(?!\s*\d+[\.\)\:\-])[^\n]+)*)',
        rf'(?:^|\n)\s*—\s*exercice\s*{n}\s*—\s*([^\n]+(?:\n(?!—\s*exercice)[^\n]+)*)',
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            ans = m.group(1).strip()
            if len(ans) >= 3:
                return ans[:2000]

    # Fallback : chercher un mot-clé du section_label
    if section_label:
        lbl = section_label.strip()
        if lbl:
            m = re.search(
                rf'(?:^|\n)\s*{re.escape(lbl)}\s*[:\.\-]?\s*([^\n]+(?:\n[^\n]+){{0,12}})',
                text,
                re.IGNORECASE | re.MULTILINE,
            )
            if m:
                return m.group(1).strip()[:2000]

    return ''


def merge_student_answers(
    qa_pairs: list,
    mise_au_net: str,
    inline_answers: Optional[list] = None,
) -> list:
    """Enrichit chaque paire avec la meilleure réponse élève disponible."""
    inline_answers = inline_answers or []
    out = []
    for i, pair in enumerate(qa_pairs):
        p = dict(pair)
        inline = (inline_answers[i] if i < len(inline_answers) else '') or ''
        inline = str(inline).strip()
        extracted = extract_answer_for_question(
            mise_au_net,
            i + 1,
            section_label=str(p.get('section', '') or ''),
            question_text=str(p.get('question', '') or ''),
        )
        if inline and len(inline) >= len(extracted):
            p['student_answer'] = inline
        elif extracted:
            p['student_answer'] = extracted
        else:
            p['student_answer'] = inline
        out.append(p)
    return out
