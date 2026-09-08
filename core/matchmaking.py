"""
Matchmaking 1v1 — file d'attente humaine, mode aléatoire, duel live.
"""
from __future__ import annotations

import random
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.live_duel import start_live_duel
from core.models import MatchQueueEntry, QuizDuel

ALEATOIRE = 'aleatoire'
QUEUE_EXPIRE_SEC = 180


def _display_name(user, viewer=None) -> str:
    from accounts.names import display_name_for, public_name
    if viewer is not None:
        return display_name_for(viewer, user) or 'Joueur'
    return public_name(user) or 'Joueur'


def cancel_user_queue(user) -> None:
    MatchQueueEntry.objects.filter(user=user, status='waiting').update(status='cancelled')


def _fetch_mixed_questions(user, count: int = 10) -> list:
    from core.views import _fetch_duel_questions, _get_user_serie_subjects

    subjects = list(_get_user_serie_subjects(user)) or ['maths', 'physique', 'svt']
    random.shuffle(subjects)
    pool = []
    per = max(2, count // max(1, len(subjects)))
    for subj in subjects:
        pool.extend(_fetch_duel_questions(subj, count=per))
    random.shuffle(pool)
    return pool[:count]


def _fetch_subject_questions(subject: str, count: int = 10) -> list:
    from core.views import _fetch_duel_questions

    return _fetch_duel_questions(subject, count=count)


@transaction.atomic
def _create_live_pair(
    creator,
    challenger,
    questions: list,
    display_subject: str,
    is_mixed: bool,
    match_mode: str = 'quick',
) -> QuizDuel:
    if not questions:
        return None

    expires = timezone.now() + timedelta(minutes=25)
    duel = QuizDuel.objects.create(
        code=QuizDuel.generate_code(),
        creator=creator,
        challenger=challenger,
        subject=display_subject,
        questions=questions,
        expires_at=expires,
        status='waiting',
        match_mode=match_mode,
        is_mixed_subjects=is_mixed,
        is_live_race=True,
    )
    start_live_duel(duel)
    return duel


def _mark_entries_matched(duel: QuizDuel, users: list) -> None:
    for u in users:
        MatchQueueEntry.objects.filter(user=u, status='waiting').update(
            status='matched', duel=duel,
        )
    try:
        from core.push_events import push_match_found
        for u in users:
            opp = duel.challenger if duel.creator_id == u.id else duel.creator
            if opp:
                push_match_found(u, opp)
    except Exception:
        pass


def _duel_match_payload(duel: QuizDuel, user) -> dict:
    opp = duel.challenger if duel.creator_id == user.id else duel.creator
    return {
        'matched': True,
        'code': duel.code,
        'opponent_name': _display_name(opp, user),
        'subject': duel.subject,
        'is_mixed': duel.is_mixed_subjects,
        'total': len(duel.questions or []),
        'live': True,
    }


@transaction.atomic
def try_find_match(user, requested_subject: str) -> QuizDuel | None:
    """Logique de pairing : aléatoire ↔ aléatoire, ou aléatoire ↔ matière précise."""
    if requested_subject == ALEATOIRE:
        other = (
            MatchQueueEntry.objects.filter(status='waiting', subject=ALEATOIRE)
            .exclude(user=user)
            .select_related('user')
            .order_by('created_at')
            .first()
        )
        if other:
            questions = _fetch_mixed_questions(user, 10)
            if not questions:
                return None
            duel = _create_live_pair(
                other.user, user, questions,
                display_subject=ALEATOIRE, is_mixed=True,
            )
            if duel:
                _mark_entries_matched(duel, [user, other.user])
            return duel

        other = (
            MatchQueueEntry.objects.filter(status='waiting')
            .exclude(subject=ALEATOIRE)
            .exclude(user=user)
            .select_related('user')
            .order_by('created_at')
            .first()
        )
        if other:
            subj = other.subject
            questions = _fetch_subject_questions(subj, 10)
            if not questions:
                return None
            duel = _create_live_pair(
                other.user, user, questions,
                display_subject=subj, is_mixed=False,
            )
            if duel:
                _mark_entries_matched(duel, [user, other.user])
            return duel

    else:
        other = (
            MatchQueueEntry.objects.filter(status='waiting', subject=requested_subject)
            .exclude(user=user)
            .select_related('user')
            .order_by('created_at')
            .first()
        )
        if other:
            questions = _fetch_subject_questions(requested_subject, 10)
            if not questions:
                return None
            duel = _create_live_pair(
                other.user, user, questions,
                display_subject=requested_subject, is_mixed=False,
            )
            if duel:
                _mark_entries_matched(duel, [user, other.user])
            return duel

        other = (
            MatchQueueEntry.objects.filter(status='waiting', subject=ALEATOIRE)
            .exclude(user=user)
            .select_related('user')
            .order_by('created_at')
            .first()
        )
        if other:
            questions = _fetch_subject_questions(requested_subject, 10)
            if not questions:
                return None
            duel = _create_live_pair(
                other.user, user, questions,
                display_subject=requested_subject, is_mixed=False,
            )
            if duel:
                _mark_entries_matched(duel, [user, other.user])
            return duel

    return None


@transaction.atomic
def enter_quick_match(user, subject: str) -> dict:
    cancel_user_queue(user)

    duel = try_find_match(user, subject)
    if duel:
        return _duel_match_payload(duel, user)

    entry = MatchQueueEntry.objects.create(user=user, subject=subject, status='waiting')
    return {
        'matched': False,
        'waiting': True,
        'queue_id': entry.id,
        'subject': subject,
    }


@transaction.atomic
def poll_quick_match(user, queue_id: int) -> dict:
    try:
        entry = MatchQueueEntry.objects.select_related('duel').get(pk=queue_id, user=user)
    except MatchQueueEntry.DoesNotExist:
        return {'error': 'File introuvable', 'code': 'not_found'}

    if entry.status == 'matched' and entry.duel_id:
        return _duel_match_payload(entry.duel, user)

    if entry.status != 'waiting':
        return {'error': 'Entrée expirée', 'code': 'expired'}

    age = (timezone.now() - entry.created_at).total_seconds()
    if age > QUEUE_EXPIRE_SEC:
        entry.status = 'expired'
        entry.save(update_fields=['status'])
        return {'error': 'Délai expiré — réessaie.', 'code': 'expired'}

    duel = try_find_match(user, entry.subject)
    if duel:
        return _duel_match_payload(duel, user)

    return {
        'matched': False,
        'waiting': True,
        'queue_id': entry.id,
        'wait_seconds': round(age, 1),
    }


def online_players_count() -> int:
    try:
        from accounts.models import UserProfile
        since = timezone.now() - timedelta(minutes=8)
        return UserProfile.objects.filter(last_seen_at__gte=since).count()
    except Exception:
        return 0


def recent_duels_for_user(user, limit=5):
    qs = QuizDuel.objects.filter(
        Q(creator=user) | Q(challenger=user),
        status='finished',
        is_ghost_opponent=False,
    ).order_by('-created_at')[:limit]
    rows = []
    for d in qs:
        is_creator = d.creator_id == user.id
        my_score = d.creator_score if is_creator else d.challenger_score
        opp_score = d.challenger_score if is_creator else d.creator_score
        if is_creator and d.challenger:
            opp_name = _display_name(d.challenger, user)
        elif not is_creator:
            opp_name = _display_name(d.creator, user)
        else:
            opp_name = '—'
        rows.append({
            'code': d.code,
            'subject': d.subject,
            'my_score': my_score,
            'opp_score': opp_score,
            'won': my_score > opp_score,
            'ghost': False,
            'opponent': opp_name,
            'date': d.created_at.isoformat(),
        })
    return rows
