"""Titres de conversation en mots-clés — sans IA.

« bonjour » → Salutation (puis le vrai sujet dès le 2e message).
« quelle est la planète la plus grosse du système solaire »
→ « Planète plus grosse · système solaire ».
"""
from __future__ import annotations

import re
import unicodedata

_GREET = frozenset({
    'bonjour', 'bonsoir', 'salut', 'hello', 'hey', 'hi', 'bjr', 'bsr',
    'bonswa', 'bonjou', 'alo', 'wesh', 'yo', 'coucou', 'slt', 'cc',
    'sak pase', 'kijan ou ye', 'comment ca va', 'ca va', 'cava',
})
_THANKS = frozenset({'merci', 'mesi', 'mèsi', 'thanks', 'thank you', 'thx'})
_BYE = frozenset({'bye', 'au revoir', 'a plus', 'a+', 'ciao', 'bonne nuit'})

_STOP = frozenset({
    'le', 'la', 'les', 'un', 'une', 'des', 'du', 'de', 'd', 'l', 'au', 'aux',
    'en', 'et', 'ou', 'est', 'sont', 'suis', 'es', 'sommes', 'etes',
    'que', 'qui', 'quoi', 'dont', 'quel', 'quelle', 'quels', 'quelles',
    'cest', 'c', 'ca', 'ce', 'cet', 'cette', 'ces',
    'mon', 'ma', 'mes', 'ton', 'ta', 'tes', 'son', 'sa', 'ses', 'notre', 'nos',
    'je', 'tu', 'il', 'elle', 'on', 'nous', 'vous', 'ils', 'elles',
    'me', 'te', 'se', 'moi', 'toi', 'lui',
    'a', 'y', 'ne', 'pas', 'ni', 'si', 'oui', 'non',
    'pour', 'par', 'avec', 'sans', 'dans', 'sur', 'sous', 'vers', 'chez',
    'comment', 'pourquoi', 'quand', 'combien', 'ou',
    'peux', 'peut', 'peuvent', 'peux-tu', 'peux tu', 'veux', 'veut', 'vouloir',
    'explique', 'expliquer', 'expliques', 'donne', 'donner', 'dis', 'dire',
    'fais', 'faire', 'aide', 'aider', 'montre', 'montrer',
    'sest', 'sil', 's', 'n', 'qu', 'j', 't', 'm',
    'the', 'an', 'is', 'of', 'to', 'in', 'for',
    'ki', 'yon', 'yo', 'ak', 'pou', 'nan', 'sa', 'kisa', 'kijan', 'gen',
    'bonjour', 'bonsoir', 'salut', 'hello', 'hey', 'hi', 'merci', 'mesi',
    'stp', 'svp', 'please', 's il te plait', 'sil te plait',
    'moi', 'donc', 'alors', 'mais', 'car', 'comme', 'aussi', 'tres', 'tout',
    'tous', 'toute', 'toutes', 'etre', 'avoir', 'fait', 'faites',
    'fut', 'ete', 'etait', 'etaient', 'sera', 'soit',
    'exemple', 'exo', 'exercice', 'exercices', 'etude', 'texte',
    'document', 'pdf', 'fichier', 'image', 'photo',
    'analyse', 'analyser', 'reviser', 'revision', 'revois',
    'question', 'questions', 'reponds', 'repondre', 'reponse',
    'parle', 'parlez', 'parler', 'parles', 'parlons',
    'sais', 'sais-tu', 'connais', 'connaitre', 'connaissez',
    'maniere', 'facon', 'simplement', 'simple', 'importe', 'important',
    'habitude', 'abitude', 'souvent', 'sortir', 'sort', 'sorte',
    'ont', 'avons', 'avez', 'as', 'ai', 'vu', 'vois', 'voici', 'voila',
    'aide-moi', 'dis-moi', 'donne-moi', 'peux-tu',
    'chapitre', 'cours', 'lecon', 'sujet', 'theme',
    'general', 'nouveau', 'nouvelle', 'conversation',
})

_KEEP_SHORT = frozenset({
    'plus', 'moins', 'gros', 'grosse', 'grand', 'grande', 'petit', 'petite',
    'bac', 'ns4', 'svt', 'pib', 'adn', 'arn', 'co2', 'h2o', 'ph',
    'pib', 'onu', 'usa',
})

_DISPLAY = {
    'haiti': 'Haïti', 'haitien': 'haïtien', 'haitienne': 'haïtienne',
    'nasa': 'NASA', 'pdf': 'PDF', 'svt': 'SVT', 'adn': 'ADN', 'arn': 'ARN',
    'president': 'président', 'presidente': 'présidente',
    'ministre': 'ministre', 'premier': 'premier',
    'derive': 'dérivé', 'derivee': 'dérivée',
    'magnetisme': 'magnétisme', 'photosynthese': 'photosynthèse',
    'domaine': 'domaine', 'fonction': 'fonction',
    'covariance': 'covariance',
}

_FILLER_MAP = (
    (_GREET, 'Salutation'),
    (_THANKS, 'Remerciement'),
    (_BYE, 'Au revoir'),
)

_WEAK_TITLES = frozenset({
    'Salutation', 'Remerciement', 'Au revoir', 'Pdf', 'PDF',
    'Étude de texte', 'Etude Texte', 'Nouvelle conversation',
})

_CANNED_PREFIX = (
    re.compile(r'^etude\s+(de\s+)?texte\b[:\s-]*', re.I),
    re.compile(r'^exemple\s+(d[e\' ]*)?(exo|exercice)s?\b[:\s-]*', re.I),
    re.compile(r'^analyse et aide-moi[^.]*\.?\s*', re.I),
    re.compile(r'^(vois|voici|voila)\s+(un\s+)?(exemple|exo|exercice)s?\b[:\s-]*', re.I),
)

_FILLER_LABELS = frozenset({'Salutation', 'Remerciement', 'Au revoir'})


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize('NFD', s)
    return ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')


def _norm(s: str) -> str:
    s = _strip_accents((s or '').strip().lower())
    s = s.replace("'", ' ').replace('’', ' ')
    s = re.sub(r'[^a-z0-9+\-^./\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _clean_source(text: str) -> str:
    s = str(text or '').strip()
    s = re.sub(r'📄\s*\*\*PDF:.*', '', s, flags=re.I | re.S)
    s = re.sub(r'📄\s*\S+', '', s)
    s = re.sub(r'Analyse et aide-moi à réviser[^.]*\.?\s*', '', s, flags=re.I)
    for rx in _CANNED_PREFIX:
        s = rx.sub('', s).strip()
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _is_filler_only(norm: str) -> str | None:
    if not norm:
        return None
    compact = re.sub(r'[!?.,]+', ' ', norm)
    compact = re.sub(r'\s+', ' ', compact).strip()
    if compact in _GREET or compact in _THANKS or compact in _BYE:
        for bucket, label in _FILLER_MAP:
            if compact in bucket:
                return label
    words = compact.split()
    if words and all(w in _GREET or w in {'ca', 'va', 'cava', 'et', 'toi'} for w in words) and len(words) <= 5:
        return 'Salutation'
    if compact in _THANKS or (len(words) <= 2 and all(w in _THANKS for w in words)):
        return 'Remerciement'
    return None


def _tokens(norm: str) -> list[str]:
    return [t for t in re.split(r'\s+', norm) if t]


def _keep(tok: str) -> bool:
    if not tok:
        return False
    if tok in _KEEP_SHORT:
        return True
    if re.search(r'\d', tok) or any(ch in tok for ch in '+-^/=.'):
        return True
    if tok in _STOP:
        return False
    return len(tok) >= 3


def _restore_display(original: str, kept_norm: list[str]) -> list[str]:
    orig_words = re.findall(r"[A-Za-zÀ-ÿ0-9+\-^./]+", original)
    by_norm = {}
    for w in orig_words:
        key = _norm(w)
        if key and key not in by_norm:
            by_norm[key] = w
    out = []
    for t in kept_norm:
        if t in ('plus', 'moins', 'et', 'ou', 'de', 'du'):
            out.append(t)
            continue
        pretty = _DISPLAY.get(t)
        raw = pretty or by_norm.get(t, t)
        if raw.isupper() and len(raw) <= 5:
            out.append(raw)
        else:
            out.append(raw[:1].upper() + raw[1:] if raw else t)
    return out


def _cluster_title(words: list[str]) -> str:
    if not words:
        return ''
    if len(words) <= 4:
        return ' '.join(words)
    head = words[:3]
    tail = words[3:6]
    return ' · '.join(p for p in (' '.join(head), ' '.join(tail)) if p)


def _subject_label(subject: str) -> str:
    labels = {
        'maths': 'Maths', 'physique': 'Physique', 'chimie': 'Chimie', 'svt': 'SVT',
        'francais': 'Kreyòl', 'philosophie': 'Philosophie', 'anglais': 'Anglais',
        'histoire': 'Sc Social', 'economie': 'Économie', 'informatique': 'Informatique',
        'art': 'Art', 'espagnol': 'Espagnol',
    }
    return labels.get((subject or '').strip().lower(), '')


def _subject_fallback(subject: str) -> str:
    label = _subject_label(subject)
    return f'{label} · discussion' if label else 'Nouvelle conversation'


def is_weak_title(title: str) -> bool:
    t = (title or '').strip()
    if not t:
        return True
    if t in _WEAK_TITLES or t in _FILLER_LABELS:
        return True
    if t.endswith('— conversation') or t.endswith('· discussion') or t.endswith('· Salutation'):
        return True
    if t.lower() in {'pdf', 'document', 'image'}:
        return True
    return False


def conversation_title(text: str, subject: str = '', max_len: int = 42) -> str:
    source = _clean_source(text)
    if not source:
        return _subject_fallback(subject)

    n = _norm(source)
    filler = _is_filler_only(n)
    if filler:
        return filler

    toks = _tokens(n)
    bridged = []
    for i, t in enumerate(toks):
        if t in ('plus', 'moins') and 0 < i < len(toks) - 1:
            if _keep(toks[i - 1]) and _keep(toks[i + 1]):
                bridged.append(t)
                continue
        if _keep(t):
            bridged.append(t)

    seen = set()
    kept = []
    for t in bridged:
        if t in seen:
            continue
        seen.add(t)
        kept.append(t)
    kept = kept[:5]

    if not kept:
        return _subject_fallback(subject)

    display = _restore_display(source, kept)
    title = _cluster_title(display)
    if len(title) > max_len:
        title = title[: max_len - 1].rstrip() + '…'
    return title or _subject_fallback(subject)


def conversation_title_from_thread(messages: list[str], subject: str = '') -> str:
    """Premier message utile (skip salutations / consignes vides)."""
    filler_seen = None
    for raw in messages or []:
        source = _clean_source(raw)
        n = _norm(source)
        filler = _is_filler_only(n) if n else 'empty'
        if filler:
            filler_seen = filler if filler != 'empty' else filler_seen
            continue
        title = conversation_title(raw, subject)
        if title and not is_weak_title(title) and title not in _FILLER_LABELS:
            return title
    label = _subject_label(subject)
    if filler_seen == 'Salutation' and label:
        return f'{label} · Salutation'
    if filler_seen and label:
        return f'{label} · {filler_seen}'
    return _subject_fallback(subject)


def persist_conversation_title(user, session_key: str, text: str, subject: str = '') -> str:
    """Enregistre un titre heuristique ; remplace un titre trop faible (ex. Salutation)."""
    title = conversation_title(text, subject)
    if not user or not session_key:
        return title
    try:
        from core.models import ChatMessage, ChatSessionSummary

        prior = list(
            ChatMessage.objects.filter(user=user, session_key=session_key, role='user')
            .order_by('created_at')
            .values_list('content', flat=True)[:6]
        )
        if text and (not prior or prior[-1] != text):
            prior = prior + [text]
        title = conversation_title_from_thread(prior, subject)

        obj, created = ChatSessionSummary.objects.get_or_create(
            user=user,
            session_key=session_key,
            defaults={
                'summary': {'title': title, 'source': 'heuristic'},
                'subjects_covered': [subject] if subject else [],
                'message_count': 1,
            },
        )
        if created:
            return title
        summ = obj.summary if isinstance(obj.summary, dict) else {}
        current = (summ.get('title') or '').strip()
        if not current or is_weak_title(current):
            if title and (not is_weak_title(title) or not current):
                summ['title'] = title
                summ.setdefault('source', 'heuristic')
                obj.summary = summ
                obj.save(update_fields=['summary'])
                return title
        return current or title
    except Exception:
        return title
