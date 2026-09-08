MAX_TEAM_SIZE = 4
MIN_PLAYERS_TO_START = 3
MIN_PLAYERS_REQUIRED = 2
ABSENCE_THRESHOLD_FOR_REMOVAL = 2

INVITE_CODE_PREFIX = 'OTB'

# Concours général hebdomadaire (heure locale Django TIME_ZONE)
WEEKLY_COMPETITION_SLUG = 'weekly-general'
REGISTRATION_WEEKDAY = 6  # dimanche (Python: lundi=0 … dimanche=6)
MATCH_WINDOW_START_HOUR = 19  # 19h inclus
MATCH_WINDOW_END_HOUR = 20    # 20h exclus

COMPETITION_STATUS = (
    'draft', 'registration', 'roster_locked', 'in_progress', 'completed', 'cancelled',
)

COMPETITION_STATUS_LABELS = {
    'draft': 'Brouillon',
    'registration': 'Inscriptions ouvertes',
    'roster_locked': 'Inscriptions fermées',
    'in_progress': 'En cours',
    'completed': 'Terminé',
    'cancelled': 'Annulé',
}

REGISTRATION_STATUS_LABELS = {
    'pending': 'En attente',
    'registered': 'Inscrite',
    'roster_locked': 'Équipe verrouillée',
    'eliminated': 'Éliminée',
    'withdrawn': 'Retirée',
}

PRIZE_PLACE_META = {
    'champion': ('🥇', 'Champion'),
    'winner': ('🥇', 'Champion'),
    '1': ('🥇', '1re place'),
    'first': ('🥇', '1re place'),
    'finalist': ('🥈', '2e place'),
    'runner_up': ('🥈', '2e place'),
    '2': ('🥈', '2e place'),
    'second': ('🥈', '2e place'),
    'semi': ('🥉', 'Demi-finaliste'),
    '3': ('🥉', '3e place'),
    'third': ('🥉', '3e place'),
}


def competition_status_label(status: str) -> str:
    key = (status or '').strip()
    return COMPETITION_STATUS_LABELS.get(key, key or '')


def registration_status_label(status: str) -> str:
    key = (status or '').strip()
    return REGISTRATION_STATUS_LABELS.get(key, key or '')

MATCH_STATUS = (
    'scheduled', 'lobby', 'in_progress', 'finished', 'forfeit', 'cancelled',
)

MATCH_PHASE = (
    'waiting', 'question_show', 'answering', 'captain_lock', 'revealing',
    'explanation', 'finished',
)

INVITATION_STATUS = ('pending', 'accepted', 'rejected', 'cancelled', 'expired')

DEFAULT_MATCH_CONFIG = {
    'question_count': 10,
    'answer_seconds': 45,
    'captain_lock_seconds': 15,
    'explanation_seconds': 10,
    'reveal_seconds': 5,
    'team_question_points': 100,
    'speed_bonus_max': 20,
}

DEFAULT_COMPETITION_CONFIG = {
    'match': DEFAULT_MATCH_CONFIG,
    'phases': [
        {'key': 'groups', 'label': 'Phase de groupes'},
        {'key': 'quarters', 'label': 'Quarts de finale'},
        {'key': 'semis', 'label': 'Demi-finales'},
        {'key': 'final', 'label': 'Finale'},
    ],
}

# XP élève en fin de concours — source unique : core.xp_config
from core.xp_config import (  # noqa: E402
    GENIUS_XP_CHAMPION,
    GENIUS_XP_FINALIST,
    GENIUS_XP_SEMI,
    GENIUS_XP_PARTICIPATION,
)
