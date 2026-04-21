"""
Données des séries du Baccalauréat : matières et coefficients.
Source : tableau officiel des épreuves.

Mapping interne → labels officiels :
  philosophie  → Philosophy
  chimie       → Chemistry
  histoire     → History and Geo / Social Sciences
  svt          → SVT / Biology
  physique     → Physics
  anglais      → English / English or Spanish
  maths        → Mathematics
  francais     → (implicite dans LLA)
  economie     → Economics (SES)
  art          → Art & Music (LLA)
  espagnol     → Spanish (LLA Thu, Philo C)
  creole       → Creole (ignoré dans les quiz)
"""

# Coefficient de base pour les matières hors-série (jamais 0, au moins 1)
DEFAULT_COEF = 100

SERIES: dict = {

    # ── Sciences de la Vie et de la Terre ─────────────────────────────────────
    'SVT': {
        'label': 'SVT — Sciences de la Vie et de la Terre',
        'icon': '🧬',
        'subjects': {
            'philosophie': 200,
            'chimie':       300,
            'histoire':     200,
            'svt':          400,   # matière principale
            'physique':     200,
            'anglais':      200,
            'maths':        200,
        },
    },

    # ── Sciences Mathématiques et Physiques ────────────────────────────────────
    'SMP': {
        'label': 'SMP — Sciences Mathématiques et Physiques',
        'icon': '⚗️',
        'subjects': {
            'philosophie': 200,
            'chimie':       200,
            'histoire':     200,
            'svt':          200,
            'physique':     300,
            'anglais':      200,
            'maths':        400,   # matière principale
        },
    },

    # ── Sciences Économiques et Sociales ───────────────────────────────────────
    'SES': {
        'label': 'SES — Sciences Économiques et Sociales',
        'icon': '📊',
        'subjects': {
            'philosophie': 200,
            'chimie':       100,
            'histoire':     400,   # matière principale
            'svt':          100,
            'physique':     100,
            'anglais':      200,
            'maths':        200,
            'economie':     400,   # matière principale (Economics)
        },
    },

    # ── Lettres, Langues et Arts ───────────────────────────────────────────────
    'LLA': {
        'label': 'LLA — Lettres, Langues et Arts',
        'icon': '📚',
        'subjects': {
            'philosophie': 300,   # matière principale
            'chimie':       100,
            'histoire':     200,
            'anglais':      300,   # matière principale
            'maths':        100,
            'francais':     200,
            'art':          300,   # matière principale (Art & Music)
        },
    },
}

# Toutes les matières qui existent (union de toutes les séries)
ALL_SUBJECTS = sorted({
    subj
    for serie in SERIES.values()
    for subj in serie['subjects']
})

# Matières "standard" disponibles dans l'app (pas economie/art/creole)
APP_SUBJECTS = ['maths', 'physique', 'chimie', 'svt', 'francais', 'philosophie', 'histoire', 'anglais']


def get_serie(serie_key: str) -> dict:
    """Retourne les données d'une série (ou SVT par défaut)."""
    return SERIES.get(serie_key, SERIES['SVT'])


def get_subject_coeff(serie_key: str, subject: str) -> int:
    """Retourne le coefficient d'une matière pour une série donnée."""
    serie = get_serie(serie_key)
    return serie['subjects'].get(subject, DEFAULT_COEF)


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
