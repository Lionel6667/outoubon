"""
Optimisation tokens chat — qualité identique, moins d'appels / moins de contexte.
"""
from __future__ import annotations

import re

from core.local_responses import normalize_text

# Caps chat (entrée) — plus stricts que le cours / exercices
CHAT_RAG_MAX_BLOCKS = 2
CHAT_RAG_MAX_CHARS = 2_200
CHAT_CONTEXT_MAX_CHARS = 2_800
CHAT_PDF_EXCERPT_CHARS = 1_400
CHAT_HISTORY_LOAD = 4

_SYNTHESIS_KEYWORDS = frozenset({
    'explique', 'expliquer', 'expliquez', 'compare', 'comparaison', 'comparer',
    'pourquoi', 'comment', 'analyse', 'analyser', 'developpe', 'developper',
    'aide moi', 'aide-moi', 'methode', 'resolution', 'resoudre', 'corrige',
    'correction', 'difference', 'differences', 'consequences', 'causes',
    'resume', 'resumer', 'plan', 'dissertation', 'biographie', 'parcours',
    'synthese', 'argument', 'demonstration', 'justifie', 'justifier',
    'interpretation', 'commentaire', 'redige', 'rediger', 'ecris', 'ecrire',
    'parle moi', 'parle de', 'tell me', 'explain', 'why', 'how', 'analyze',
    'compare', 'summary', 'develop', 'esplik', 'kijan', 'poukisa', 'analiz',
})

_EXPLAIN_EXO_KEYWORDS = frozenset({
    'explique', 'corrige', 'correction', 'resolution', 'resoudre', 'solution',
    'demarche', 'etape', 'etapes', 'aide moi', 'aide-moi', 'comment faire',
    'how to', 'solve', 'walk me', 'guide',
})

_DEFINITIONAL_PREFIXES = (
    'c est quoi', "c'est quoi", 'qu est ce que', "qu'est-ce que", 'qu est ce',
    'definition de', 'definition du', 'definition d', 'definir', 'definis',
    'kisa se', 'ki sa se', 'what is', 'what are', 'define', 'meaning of',
    'liste des', 'list of', 'les types de', 'types de',
)

_PROFILE_TRIGGERS = frozenset({
    'mon niveau', 'ma serie', 'faible', 'difficile', 'comprends pas',
    'pa kompran', 'debutant', 'beginner', 'avance', 'advanced', 'mon profil',
})

_PDF_RE = re.compile(
    r'\n\n📄 \*\*PDF:\s*(?P<name>.*?)\*\*\n(?P<body>[\s\S]*)$',
    re.IGNORECASE,
)


def split_pdf_from_message(text: str) -> tuple[str, str, str]:
    """
    Sépare la question utilisateur et le texte PDF injecté côté client.
    Retourne (query_clean, pdf_name, pdf_excerpt).
    """
    raw = (text or '').strip()
    if not raw:
        return '', '', ''

    m = _PDF_RE.search(raw)
    if not m:
        return raw, '', ''

    query = raw[:m.start()].strip()
    pdf_name = (m.group('name') or 'document.pdf').strip()
    pdf_body = (m.group('body') or '').strip()
    if len(pdf_body) > CHAT_PDF_EXCERPT_CHARS:
        pdf_body = pdf_body[:CHAT_PDF_EXCERPT_CHARS].rstrip() + '…'

    if not query:
        query = f'Analyse ce document : {pdf_name}'

    return query, pdf_name, pdf_body


def build_pdf_context(pdf_name: str, pdf_excerpt: str) -> str:
    if not pdf_excerpt:
        return ''
    return f"📄 Document joint ({pdf_name}) — extrait :\n{pdf_excerpt}"


def needs_user_profile(message: str, history_len: int) -> bool:
    if history_len == 0:
        return True
    norm = normalize_text(message)
    return any(t in norm for t in _PROFILE_TRIGGERS)


def wants_exercise_explanation(message: str) -> bool:
    norm = normalize_text(message)
    return any(kw in norm for kw in _EXPLAIN_EXO_KEYWORDS)


def is_definitional_lookup(message: str) -> bool:
    norm = normalize_text(message)
    if len(norm) > 160:
        return False
    return any(norm.startswith(p) or f' {p} ' in f' {norm} ' for p in _DEFINITIONAL_PREFIXES)


def needs_ai_synthesis(
    message: str,
    *,
    history_len: int = 0,
    has_image: bool = False,
    has_pdf: bool = False,
    rag_top_score: float = 0,
) -> bool:
    """True → appel modèle ; False → réponse locale formatée suffit."""
    if has_image:
        return True
    if has_pdf and any(
        kw in normalize_text(message)
        for kw in ('analyse', 'resume', 'synthese', 'corrige', 'explique', 'comment')
    ):
        return True

    norm = normalize_text(message)
    if not norm:
        return False

    if history_len > 0:
        # Suite de conversation : modèle pour la continuité pédagogique
        return True

    if len(norm) > 220:
        return True

    if any(kw in norm for kw in _SYNTHESIS_KEYWORDS):
        return True

    if wants_exercise_explanation(message):
        return True

    # Lookup factuel avec bon match RAG → pas besoin du modèle
    if rag_top_score >= 800 and (is_definitional_lookup(message) or len(norm) <= 90):
        return False

    if rag_top_score >= 1200 and len(norm) <= 120:
        return False

    # Question courte sans signal de synthèse → local si on a du contenu
    if rag_top_score >= 600 and len(norm) <= 70:
        return False

    return True


def format_local_chat_reply(
    subject_label: str,
    body: str,
    *,
    chapter: str = '',
    subchapter: str = '',
    source: str = 'programme BAC (notes locales)',
) -> str:
    lines = [f"### {subject_label}"]
    if chapter:
        head = f"**{chapter}**"
        if subchapter:
            head += f" — {subchapter}"
        lines.append(head)
    lines.append('')
    lines.append((body or '').strip())
    lines.append('')
    lines.append(f"_Source : {source}_")
    lines.append('')
    lines.append("Tu veux que j'explique un point plus en détail ou qu'on fasse un exercice ?")
    return '\n'.join(lines)


def trim_chat_context(context: str, max_chars: int = CHAT_CONTEXT_MAX_CHARS) -> str:
    ctx = (context or '').strip()
    if len(ctx) <= max_chars:
        return ctx
    return ctx[:max_chars].rstrip() + '\n\n…'
