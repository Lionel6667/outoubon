"""
Duel live 1v1 — première bonne réponse gagne la question (timer court).
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import QuizDuel

QUESTION_TIME_SEC = 22
REVEAL_TIME_SEC = 2.8


def _display_name(user, viewer=None) -> str:
    if not user:
        return '—'
    from accounts.names import display_name_for, public_name
    if viewer is not None:
        return display_name_for(viewer, user) or 'Joueur'
    return public_name(user) or 'Joueur'


def _advance_after_timeout(duel: QuizDuel) -> None:
    duel.live_phase = 'reveal'
    duel.question_winner = None
    duel.reveal_deadline = timezone.now() + timedelta(seconds=REVEAL_TIME_SEC)
    duel.save(update_fields=['live_phase', 'question_winner', 'reveal_deadline'])


def _advance_to_next(duel: QuizDuel) -> None:
    total = len(duel.questions or [])
    next_idx = duel.current_q_index + 1
    if next_idx >= total:
        duel.live_phase = 'finished'
        duel.status = 'finished'
        duel.creator_finished = True
        duel.challenger_finished = True
        duel.question_deadline = None
        duel.reveal_deadline = None
        duel.question_winner = None
        duel.save()
        try:
            from core.push_events import push_duel_finished
            if duel.challenger_id and not getattr(duel, 'is_ghost_opponent', False):
                c_won = (duel.creator_score or 0) > (duel.challenger_score or 0)
                ch_won = (duel.challenger_score or 0) > (duel.creator_score or 0)
                push_duel_finished(duel.creator, duel.challenger, c_won)
                push_duel_finished(duel.challenger, duel.creator, ch_won)
        except Exception:
            pass
        return

    now = timezone.now()
    duel.current_q_index = next_idx
    duel.live_phase = 'active'
    duel.question_winner = None
    duel.question_deadline = now + timedelta(seconds=QUESTION_TIME_SEC)
    duel.reveal_deadline = None
    duel.save(
        update_fields=[
            'current_q_index', 'live_phase', 'question_winner',
            'question_deadline', 'reveal_deadline', 'status',
            'creator_finished', 'challenger_finished',
        ],
    )


def tick_live_duel(duel: QuizDuel) -> QuizDuel:
    """Avance phases reveal / timeout si délais expirés."""
    if not duel.is_live_race or duel.status != 'active':
        return duel

    now = timezone.now()
    if duel.live_phase == 'reveal' and duel.reveal_deadline and now >= duel.reveal_deadline:
        _advance_to_next(duel)
        duel.refresh_from_db()
        return duel

    if duel.live_phase == 'active' and duel.question_deadline and now >= duel.question_deadline:
        if not duel.question_winner_id:
            _advance_after_timeout(duel)
            duel.refresh_from_db()
    return duel


def start_live_duel(duel: QuizDuel) -> QuizDuel:
    now = timezone.now()
    duel.is_live_race = True
    duel.status = 'active'
    duel.current_q_index = 0
    duel.live_phase = 'active'
    duel.creator_score = 0
    duel.challenger_score = 0
    duel.creator_finished = False
    duel.challenger_finished = False
    duel.question_winner = None
    duel.question_deadline = now + timedelta(seconds=QUESTION_TIME_SEC)
    duel.reveal_deadline = None
    duel.save()
    return duel


@transaction.atomic
def submit_live_answer(duel: QuizDuel, user, choice_idx: int) -> dict:
    duel = QuizDuel.objects.select_for_update().get(pk=duel.pk)
    tick_live_duel(duel)
    duel.refresh_from_db()

    if duel.status == 'finished' or duel.live_phase == 'finished':
        return {'ok': False, 'error': 'match_finished'}

    if duel.live_phase != 'active':
        return {'ok': False, 'error': 'not_active', 'phase': duel.live_phase}

    now = timezone.now()
    if duel.question_deadline and now > duel.question_deadline:
        return {'ok': False, 'error': 'too_late'}

    if duel.question_winner_id:
        winner = duel.question_winner
        return {
            'ok': False,
            'error': 'already_won',
            'winner_name': _display_name(winner, user),
            'winner_is_me': winner.id == user.id,
        }

    questions = duel.questions or []
    idx = duel.current_q_index
    if idx >= len(questions):
        return {'ok': False, 'error': 'no_question'}

    q = questions[idx]
    try:
        correct_idx = int(q.get('reponse_correcte', 0))
        chosen = int(choice_idx)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'invalid_choice'}

    is_correct = chosen == correct_idx
    if not is_correct:
        return {
            'ok': True,
            'correct': False,
            'race_open': True,
            'message': 'Mauvaise réponse — ton adversaire peut encore gagner la manche.',
        }

    duel.question_winner = user
    if user.id == duel.creator_id:
        duel.creator_score += 1
    else:
        duel.challenger_score += 1
    duel.live_phase = 'reveal'
    duel.reveal_deadline = now + timedelta(seconds=REVEAL_TIME_SEC)
    duel.save(
        update_fields=[
            'question_winner', 'creator_score', 'challenger_score',
            'live_phase', 'reveal_deadline',
        ],
    )

    return {
        'ok': True,
        'correct': True,
        'won_round': True,
        'winner_name': _display_name(user, user),
        'winner_is_me': True,
        'my_score': duel.creator_score if user.id == duel.creator_id else duel.challenger_score,
        'phase': 'reveal',
    }


def live_state_payload(duel: QuizDuel, user) -> dict:
    is_creator = duel.creator_id == user.id
    is_challenger = duel.challenger_id == user.id
    if not is_creator and not is_challenger:
        return {'error': 'forbidden'}

    if duel.status == 'waiting':
        opp_label = 'En attente…' if is_creator else _display_name(duel.creator, user)
        return {
            'code': duel.code,
            'status': 'waiting',
            'live_phase': 'waiting',
            'q_index': 0,
            'total': len(duel.questions or []),
            'my_score': 0,
            'opp_score': 0,
            'opponent_name': opp_label,
            'question': None,
            'deadline_ms': None,
            'reveal_ms': None,
            'question_time_sec': QUESTION_TIME_SEC,
            'last_winner_name': '',
            'last_winner_is_me': False,
            'last_round_won': False,
            'is_mixed': duel.is_mixed_subjects,
            'subject': duel.subject,
            'is_live_race': False,
        }

    duel = tick_live_duel(duel)

    opp = duel.challenger if is_creator else duel.creator
    my_score = duel.creator_score if is_creator else duel.challenger_score
    opp_score = duel.challenger_score if is_creator else duel.creator_score

    questions = duel.questions or []
    idx = duel.current_q_index
    q_data = None
    if duel.live_phase == 'active' and idx < len(questions):
        q = questions[idx]
        q_data = {
            'enonce': q.get('enonce', ''),
            'options': q.get('options', []),
            'theme': q.get('theme', ''),
        }

    last_winner = duel.question_winner
    last_winner_name = _display_name(last_winner, user) if last_winner and duel.live_phase == 'reveal' else ''

    now = timezone.now()
    deadline_ms = None
    reveal_ms = None
    if duel.question_deadline and duel.live_phase == 'active':
        deadline_ms = int(duel.question_deadline.timestamp() * 1000)
    if duel.reveal_deadline and duel.live_phase == 'reveal':
        reveal_ms = int(duel.reveal_deadline.timestamp() * 1000)

    return {
        'code': duel.code,
        'status': duel.status,
        'live_phase': duel.live_phase,
        'q_index': idx,
        'total': len(questions),
        'my_score': my_score,
        'opp_score': opp_score,
        'opponent_name': _display_name(opp, user),
        'question': q_data,
        'deadline_ms': deadline_ms,
        'reveal_ms': reveal_ms,
        'question_time_sec': QUESTION_TIME_SEC,
        'last_winner_name': last_winner_name,
        'last_winner_is_me': bool(last_winner and last_winner.id == user.id),
        'last_round_won': bool(last_winner),
        'is_mixed': duel.is_mixed_subjects,
        'subject': duel.subject,
        'is_live_race': duel.is_live_race,
    }
