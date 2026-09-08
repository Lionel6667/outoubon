"""
Économie XP unique — tous les montants et plafonds vivent ici.

Règle métier :
  - Beaucoup d'XP d'activité (engagement), mais 1 HTG vaut beaucoup d'XP.
  - L'argent réel vient surtout des CONCOURS et du PARRAINAGE (150 HTG).

Taux unique : 1 XP = 0,01 HTG  →  100 XP = 1 HTG.
"""

# 1 XP = 0.01 HTG (100 XP = 1 HTG). Unique pour toutes les sources.
XP_VALUE_HTG = 0.01

# Parrainage élève (150 HTG → 15 000 XP via htg_to_xp).
REFERRAL_REWARD_HTG = 150

# ── Plafonds activité → HTG / mois ──────────────────────────────────────────
ACTIVITY_MONTHLY_HTG_BUDGET_FREE = 9       # ~900 XP
ACTIVITY_MONTHLY_HTG_BUDGET_PREMIUM = 49   # ~4 900 XP

# Filets XP (alignés sur le taux ci-dessus).
ACTIVITY_MONTHLY_XP_CAP_FREE = 900
ACTIVITY_MONTHLY_XP_CAP_PREMIUM = 4900

ACTIVITY_SOURCES = frozenset({
    'QUIZ', 'EXERCISE', 'COURSE', 'DAILY_MISSION', 'STREAK', 'EXAM', 'OTHER',
})

# ── Activité : montants « comme avant », proportionnels à la note ───────────
XP_QUIZ_MAX = 20
XP_QUIZ_PERFECT_BONUS = 5  # si score serveur ≥ 80 %
XP_EXERCISE_MAX = 50
XP_SOLVE_ORPHAN = 10
XP_COURSE_CHAPTER = 25
XP_EXAM_MAX = 40

# Compat anciens noms
XP_QUIZ = XP_QUIZ_MAX
XP_EXERCISE = XP_EXERCISE_MAX
XP_EXAM = XP_EXAM_MAX

# Missions (bonus quotidien, aussi proportionnel)
XP_MISSION_QUIZ_MAX = 20
XP_MISSION_EXO_MAX = 50
XP_MISSION_CHAT = 5
XP_MISSION_BONUS_MAX = 15

XP_MISSION_QUIZ = XP_MISSION_QUIZ_MAX
XP_MISSION_EXO = XP_MISSION_EXO_MAX
XP_MISSION_BONUS_MISTAKES = XP_MISSION_BONUS_MAX
XP_MISSION_BONUS_WEAK = XP_MISSION_BONUS_MAX

XP_STREAK_DAILY = 5
XP_STREAK_MILESTONES = {
    3: 15,
    7: 40,
    14: 80,
    30: 150,
}

# ── Concours (valeur HTG réelle) ────────────────────────────────────────────
# À 0,01 HTG/XP : champion = 200 HTG, finaliste = 100, demi = 50, participation = 10
GENIUS_XP_CHAMPION = 20000
GENIUS_XP_FINALIST = 10000
GENIUS_XP_SEMI = 5000
GENIUS_XP_PARTICIPATION = 1000

DAILY_CAP_QUIZ = 5
DAILY_CAP_EXERCISE = 5
DAILY_CAP_SOLVE = 2
DAILY_CAP_COURSE = 4
DAILY_CAP_EXAM = 1
DAILY_CAP_STREAK = 1

QUIZ_MIN_SECONDS = 25
QUIZ_MIN_ANSWER_RATIO = 0.6
EXAM_MIN_SECONDS = 90
EXERCISE_MIN_SECONDS = 20

LEAGUE_TIER_XP = 1000

SOURCE_QUIZ = 'QUIZ'
SOURCE_EXERCISE = 'EXERCISE'
SOURCE_COURSE = 'COURSE'
SOURCE_DAILY_MISSION = 'DAILY_MISSION'
SOURCE_STREAK = 'STREAK'
SOURCE_REFERRAL = 'REFERRAL'
SOURCE_GENIUS = 'GENIUS_COMPETITION'
SOURCE_ACHIEVEMENT = 'ACHIEVEMENT'
SOURCE_ADMIN = 'ADMIN'
SOURCE_OTHER = 'OTHER'
SOURCE_LEGACY = 'LEGACY'
SOURCE_EXAM = 'EXAM'
SOURCE_WITHDRAWAL = 'WITHDRAWAL'

# Retrait élève (MonCash). 100 HTG = 10 000 XP au taux 0,01.
MIN_WITHDRAWAL_HTG = 100


def htg_to_xp(htg_amount):
    """Convertit des HTG en XP via le taux unique."""
    rate = XP_VALUE_HTG
    if rate is None or float(rate) <= 0:
        return 0
    return int(round(float(htg_amount) / float(rate)))


def xp_to_htg(xp_amount):
    """Convertit des XP en HTG via le même taux unique."""
    rate = XP_VALUE_HTG
    if rate is None or float(rate) <= 0:
        return None
    return float(xp_amount) * float(rate)


def score_ratio(score, total) -> float:
    try:
        s = float(score)
        t = float(total)
    except (TypeError, ValueError):
        return 0.0
    if t <= 0:
        return 0.0
    return max(0.0, min(1.0, s / t))


def xp_from_score(max_xp: int, score=None, total=None, ratio=None) -> int:
    """XP proportionnel à la note, plafonné à max_xp."""
    max_xp = int(max_xp or 0)
    if max_xp <= 0:
        return 0
    if ratio is None:
        ratio = score_ratio(score, total)
    else:
        try:
            ratio = max(0.0, min(1.0, float(ratio)))
        except (TypeError, ValueError):
            ratio = 0.0
    if ratio <= 0:
        return 0
    # Au moins 1 XP dès 10 % pour que les gratuits gagnent aussi
    if ratio < 0.1:
        return 0
    return max(1, int(round(max_xp * ratio)))
