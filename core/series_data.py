"""
Données officielles des séries du Baccalauréat Haïtien (MENFP - Nouveau Secondaire 4 / NS4).
Total = EXACTEMENT 1900 points pour chaque série.
"""

# Coefficient de base pour les matières hors-série (jamais 0, au moins 100)
DEFAULT_COEF = 100

SERIES: dict = {
    # ── Sciences de la Vie et de la Terre (Total: 1900 pts) ─────────────────
    'SVT': {
        'label': 'SVT — Sciences de la Vie et de la Terre',
        'icon': '🧬',
        'subjects': {
            'svt':          400,   # Matière principale (Biologie & Géologie)
            'chimie':       300,   # Spécialité
            'physique':     200,
            'maths':        200,
            'francais':     200,   # Épreuve officielle Kreyòl (clé interne francais)
            'philosophie': 200,
            'histoire':     200,   # Histoire-Géo / Sciences Sociales
            'anglais':      200,   # Langue vivante
        },
    },
    # ── Sciences Mathématiques et Physiques (Total: 1900 pts) ────────────────
    'SMP': {
        'label': 'SMP — Sciences Mathématiques et Physiques',
        'icon': '⚗️',
        'subjects': {
            'maths':        400,   # Matière principale
            'physique':     300,   # Spécialité
            'chimie':       200,
            'svt':          200,
            'francais':     200,   # Épreuve officielle Kreyòl (clé interne francais)
            'philosophie': 200,
            'histoire':     200,   # Histoire-Géo / Sciences Sociales
            'anglais':      200,   # Langue vivante
        },
    },
    # ── Sciences Économiques et Sociales (Total: 1900 pts) ───────────────────
    'SES': {
        'label': 'SES — Sciences Économiques et Sociales',
        'icon': '📊',
        'subjects': {
            'economie':     400,   # Matière principale
            'histoire':     400,   # Sciences Sociales (Matière principale)
            'maths':        200,
            'philosophie': 200,
            'francais':     200,   # Épreuve officielle Kreyòl (clé interne francais)
            'anglais':      200,
            'physique':     100,
            'chimie':       100,
            'svt':          100,
        },
    },
    # ── Lettres, Langues et Arts (Total: 1900 pts) ───────────────────────────
    'LLA': {
        'label': 'LLA — Lettres, Langues et Arts',
        'icon': '📚',
        'subjects': {
            'philosophie': 300,   # Matière principale
            'anglais':      300,   # Langue vivante 1 (Matière principale)
            'art':          300,   # Art & Musique (Matière principale)
            'francais':     200,   # Épreuve officielle Kreyòl (clé interne francais)
            'espagnol':     200,   # Langue vivante 2
            'histoire':     200,   # Sciences Sociales
            'maths':        100,
            'physique':     100,
            'chimie':       100,
            'svt':          100,
        },
    },
}

# Toutes les matières qui existent (union de toutes les séries)
ALL_SUBJECTS = sorted({
    subj
    for serie in SERIES.values()
    for subj in serie['subjects']
})

# Matières "standard" disponibles dans l'app
APP_SUBJECTS = ['maths', 'physique', 'chimie', 'svt', 'francais', 'philosophie', 'histoire', 'anglais', 'espagnol', 'economie', 'art']


def get_serie(serie_key: str) -> dict:
    """Retourne les données d'une série (ou SVT par défaut)."""
    return SERIES.get(serie_key, SERIES['SVT'])


def get_subject_coeff(serie_key: str, subject: str) -> int:
    """Retourne le coefficient d'une matière pour une série donnée."""
    serie = get_serie(serie_key)
    return serie['subjects'].get(subject, DEFAULT_COEF)


def get_exam_total_points(serie_key: str, subject: str) -> int:
    """Note officielle de l'épreuve (100 / 200 / 300 / 400), jamais un petit coefficient 2–4."""
    raw = get_subject_coeff(serie_key, subject)
    if raw <= 10:
        return max(100, int(raw) * 100)
    if raw < 100:
        return 100
    return int(raw)


def get_priority_subjects(serie_key: str, top_n: int = 3) -> list[str]:
    """
    Retourne les N matières avec les plus forts coefficients pour une série.
    Filtrées aux matières disponibles dans l'app.
    """
    serie = get_serie(serie_key)
    app_subjs = {
        s: c for s, c in serie['subjects'].items()
        if s in APP_SUBJECTS
    }
    sorted_subjs = sorted(app_subjs.items(), key=lambda x: x[1], reverse=True)
    return [s for s, _ in sorted_subjs[:top_n]]


def get_serie_context_text(serie_key: str) -> str:
    """
    Retourne un texte descriptif pour les prompts IA :
    matières prioritaires + coefficients.
    """
    serie = get_serie(serie_key)
    app_subjs = {
        s: c for s, c in serie['subjects'].items()
        if s in APP_SUBJECTS
    }
    sorted_subjs = sorted(app_subjs.items(), key=lambda x: x[1], reverse=True)
    from core.gemini import MATS  # import local pour éviter circulaire
    lines = [f"Série : {serie['label']}", "Coefficients aux épreuves du Bac :"]
    for subj, coef in sorted_subjs:
        label = MATS.get(subj, subj)
        marker = " ← PRIORITAIRE" if coef >= 300 else ""
        lines.append(f"  • {label} : {coef}{marker}")
    return '\n'.join(lines)


def choices_list() -> list[tuple[str, str]]:
    """Pour le champ Django choices."""
    return [(k, v['label']) for k, v in SERIES.items()]
