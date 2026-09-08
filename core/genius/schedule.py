"""Calendrier du concours général hebdomadaire."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from core.xp_config import GENIUS_XP_CHAMPION, GENIUS_XP_FINALIST

from .constants import (
    MATCH_WINDOW_END_HOUR,
    MATCH_WINDOW_START_HOUR,
    REGISTRATION_WEEKDAY,
    WEEKLY_COMPETITION_SLUG,
)
from .models import GeniusCompetition


def _local_now(now=None):
    now = now or timezone.now()
    return timezone.localtime(now)


def week_key_for_sunday(sunday_date) -> str:
    iso = sunday_date.isocalendar()
    return f'{iso[0]}-W{iso[1]:02d}'


def sunday_of_week(dt) -> datetime.date:
    local = timezone.localtime(dt) if timezone.is_aware(dt) else dt
    days_since_sunday = (local.weekday() + 1) % 7
    return (local - timedelta(days=days_since_sunday)).date()


def is_registration_open(now=None) -> bool:
    """Inscriptions ouvertes toute la journée du dimanche (heure locale)."""
    return _local_now(now).weekday() == REGISTRATION_WEEKDAY


def is_match_window(dt) -> bool:
    """Matchs entre 19h inclus et 20h exclus (heure locale)."""
    local = timezone.localtime(dt) if timezone.is_aware(dt) else dt
    return MATCH_WINDOW_START_HOUR <= local.hour < MATCH_WINDOW_END_HOUR


def validate_match_schedule(scheduled_at) -> Tuple[bool, str]:
    if not is_match_window(scheduled_at):
        return False, (
            f'Les matchs du concours se jouent entre '
            f'{MATCH_WINDOW_START_HOUR}h et {MATCH_WINDOW_END_HOUR}h.'
        )
    return True, ''


@transaction.atomic
def ensure_weekly_competition(now=None, created_by=None) -> GeniusCompetition:
    """
    Crée / met à jour le concours général de la semaine en cours (dimanche → samedi).
    - Dimanche : status=registration
    - Lundi+ : inscriptions fermées (roster_locked si encore en registration)
    """
    local = _local_now(now)
    sunday = sunday_of_week(local)
    key = week_key_for_sunday(sunday)
    name = f'Concours général — semaine {sunday.isocalendar()[1]}'
    end = sunday + timedelta(days=6)

    comp = GeniusCompetition.objects.filter(config__week_key=key).first()
    if comp is None:
        comp = GeniusCompetition.objects.filter(
            start_date=sunday,
            name__startswith='Concours général',
        ).first()

    wd = local.weekday()  # Mon=0 … Sun=6
    if wd == REGISTRATION_WEEKDAY:
        desired = 'registration'
    elif local.date() >= sunday:
        # Lundi→samedi de la semaine du concours
        desired = 'roster_locked'
    else:
        desired = 'draft'

    if comp is None:
        return GeniusCompetition.objects.create(
            name=name,
            description=(
                'Concours général hebdomadaire OU TOU BON. '
                'Inscriptions le dimanche uniquement. Matchs entre 19h et 20h '
                '(plusieurs matchs en parallèle possibles).'
            ),
            status=desired,
            start_date=sunday,
            end_date=end,
            max_teams=64,
            created_by=created_by,
            config={
                'week_key': key,
                'slug': WEEKLY_COMPETITION_SLUG,
                'registration_weekday': REGISTRATION_WEEKDAY,
                'match_window': [MATCH_WINDOW_START_HOUR, MATCH_WINDOW_END_HOUR],
                'weekly': True,
            },
            prizes={
                'champion': f'{GENIUS_XP_CHAMPION} XP',
                'finalist': f'{GENIUS_XP_FINALIST} XP',
            },
        )

    # Ne pas écraser un concours déjà lancé / terminé
    if comp.status in ('in_progress', 'completed', 'cancelled'):
        return comp

    cfg = dict(comp.config or {})
    cfg.update({
        'week_key': key,
        'slug': WEEKLY_COMPETITION_SLUG,
        'weekly': True,
        'match_window': [MATCH_WINDOW_START_HOUR, MATCH_WINDOW_END_HOUR],
        'registration_weekday': REGISTRATION_WEEKDAY,
    })
    updates = []
    if cfg != (comp.config or {}):
        comp.config = cfg
        updates.append('config')
    if desired == 'registration' and comp.status in ('draft', 'roster_locked'):
        comp.status = 'registration'
        updates.append('status')
    elif desired == 'roster_locked' and comp.status == 'registration':
        # Lundi : plus d'inscriptions
        comp.status = 'roster_locked'
        updates.append('status')
    if comp.start_date != sunday:
        comp.start_date = sunday
        updates.append('start_date')
    if comp.end_date != end:
        comp.end_date = end
        updates.append('end_date')
    if updates:
        comp.save(update_fields=list(dict.fromkeys(updates + ['updated_at'])))
    return comp


def current_weekly_competition(now=None) -> Optional[GeniusCompetition]:
    return ensure_weekly_competition(now=now)
