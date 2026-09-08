"""
Réponses locales sans appel API — salutations, fillers, FAQ courantes.
"""
from __future__ import annotations

import re
import unicodedata

_FILLERS = frozenset({
    'bonjour', 'bonsoir', 'salut', 'alo', 'bonswa', 'hey', 'hi', 'hello',
    'merci', 'mesi', 'ok', 'dako', 'super', 'continue', 'continuer', 'continu',
    'oui', 'wi', 'non', 'next', 'suivant', 'suite', '[auto_continue]',
    'bien', 'cool', 'parfait', 'genial', 'génial', 'compris', 'j ai compris',
    "j'ai compris", 'ok j ai compris', "ok j'ai compris",
})

# (tokens requis ⊆ question normalisée, réponse FR)
_FAQ_PATTERNS: list[tuple[frozenset, str]] = [
    (
        frozenset({'demarche', 'exercice'}),
        (
            "Pour un exercice BAC, suis cette démarche :\n"
            "1. **Lis l'énoncé** et note les données + l'inconnu\n"
            "2. **Choisis la formule** ou la loi adaptée\n"
            "3. **Calcule étape par étape** en vérifiant les unités\n"
            "4. **Conclus** avec une phrase claire\n\n"
            "Envoie-moi l'énoncé ou une photo si tu veux qu'on le fasse ensemble."
        ),
    ),
    (
        frozenset({'formule', 'vitesse'}),
        (
            "Formules vitesse (Physique BAC) :\n"
            "- Vitesse moyenne : $v = \\dfrac{\\Delta d}{\\Delta t}$\n"
            "- Vitesse instantanée : $v = \\dfrac{dx}{dt}$\n"
            "- MRUA : $v = v_0 + at$ et $d = v_0 t + \\frac{1}{2}at^2$\n\n"
            "Quelle situation veux-tu appliquer ?"
        ),
    ),
    (
        frozenset({'formule', 'acceleration'}),
        (
            "Accélération : $a = \\dfrac{\\Delta v}{\\Delta t}$ (variation de vitesse / temps).\n"
            "En chute libre près de la Terre : $g \\approx 9{,}81\\,\\text{m/s}^2$ vers le bas."
        ),
    ),
    (
        frozenset({'c', 'est', 'quoi', 'derivee'}),
        (
            "La **dérivée** mesure la vitesse de variation d'une fonction.\n"
            "Notation : $f'(x)$ ou $\\dfrac{df}{dx}$.\n"
            "Exemple : si $f(x)=x^2$, alors $f'(x)=2x$."
        ),
    ),
    (
        frozenset({'c', 'est', 'quoi', 'mitose'}),
        (
            "La **mitose** est une division cellulaire qui produit **2 cellules filles identiques** "
            "(même nombre de chromosomes que la cellule mère). "
            "Elle sert à la croissance et au renouvellement des tissus."
        ),
    ),
]


def normalize_text(text: str) -> str:
    s = (text or '').strip().lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def is_conversation_filler(message: str) -> bool:
    m = normalize_text(message)
    if not m or len(m) > 48:
        return False
    if m in _FILLERS:
        return True
    if m.startswith('auto continue') or m.startswith('regen truncated'):
        return True
    return m.rstrip('!.?') in _FILLERS


def _filler_reply(message: str, subject_label: str = '', chapter_title: str = '', user_lang: str = 'fr') -> str:
    m = normalize_text(message)
    subj = subject_label or 'ton cours'
    chap = chapter_title or 'ce chapitre'

    if user_lang == 'kr':
        if m in ('merci', 'mesi'):
            return "Ak anpil plezi! Kontinye, m ap la pou ede w. 💪"
        if m in ('ok', 'dako', 'compris', 'j ai compris', "j'ai compris", 'wi', 'oui'):
            return "Trè byen! Ki lòt pwen ou vle klèifye nan kou a?"
        return f"Bonjou! Mwen la pou ede w ak {subj}. Ki kesyon ou genyen sou {chap}?"

    if m in ('merci', 'mesi'):
        return "Avec plaisir ! Continue comme ça, je suis là si tu as d'autres questions. 💪"
    if m in ('ok', 'dako', 'compris', 'j ai compris', "j'ai compris", 'wi', 'oui', 'super', 'parfait', 'genial', 'génial'):
        return "Parfait ! Quel point veux-tu approfondir ou clarifier ?"
    if m in ('continue', 'continuer', 'continu', 'suivant', 'suite', 'next'):
        return "D'accord, on avance. Dis-moi ce qui n'est pas clair sur la partie actuelle, ou pose ta question."
    return (
        f"Bonjour ! Je suis ton tuteur **{subj}**. "
        f"On travaille sur **{chap}** — quelle question veux-tu qu'on voie ensemble ?"
    )


def _faq_match(message: str) -> str | None:
    norm = normalize_text(message)
    if len(norm) < 8 or len(norm) > 200:
        return None
    tokens = set(re.findall(r'\b[a-z0-9]{3,}\b', norm))
    if not tokens:
        return None
    best_score = 0
    best_answer = None
    for required, answer in _FAQ_PATTERNS:
        overlap = len(required & tokens)
        if overlap == 0:
            continue
        score = overlap / max(1, len(required))
        if score >= 0.85 and overlap >= len(required) - 1:
            if score > best_score:
                best_score = score
                best_answer = answer
    return best_answer


def _cached_faq_lookup(subject: str, message: str) -> str | None:
    try:
        from django.core.cache import cache
        key = f'chat_faq:{subject}:{normalize_text(message)[:120]}'
        hit = cache.get(key)
        if isinstance(hit, str) and hit.strip():
            return hit.strip()
    except Exception:
        pass
    return None


def cache_faq_answer(subject: str, question: str, answer: str, ttl: int = 86400 * 7) -> None:
    """Met en cache une paire Q/R après une réponse IA réussie (best-effort)."""
    q = normalize_text(question)
    if len(q) < 12 or len(answer or '') < 40:
        return
    try:
        from django.core.cache import cache
        key = f'chat_faq:{subject}:{q[:120]}'
        cache.set(key, answer.strip()[:4000], timeout=ttl)
    except Exception:
        pass


def try_local_chat_response(
    message: str,
    subject: str = 'general',
    user_lang: str = 'fr',
    *,
    subject_label: str = '',
    chapter_title: str = '',
    has_image: bool = False,
) -> str | None:
    """
    Retourne une réponse locale ou None si l'API doit être appelée.
    """
    if has_image:
        return None
    text = (message or '').strip()
    if not text:
        return None

    if is_conversation_filler(text):
        return _filler_reply(text, subject_label=subject_label, chapter_title=chapter_title, user_lang=user_lang)

    cached = _cached_faq_lookup(subject, text)
    if cached:
        return cached

    faq = _faq_match(text)
    if faq:
        return faq

    return None
