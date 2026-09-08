"""
Notifications, classement, stats équipe — phases 3 & 4.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import (
    GeniusCompetition,
    GeniusMatch,
    GeniusNotification,
    GeniusRegistration,
    GeniusTeam,
    GeniusTeamStats,
)

User = get_user_model()

WIN_POINTS = 3
DRAW_POINTS = 1
WIN_XP_BASE = 25
STREAK_XP_BONUS = 5


def _dashboard_path(name: str, *args) -> str:
    return reverse(name, args=args)


def _get_or_create_stats(team: GeniusTeam) -> GeniusTeamStats:
    stats, _ = GeniusTeamStats.objects.get_or_create(team=team)
    return stats


@transaction.atomic
def create_notification(
    user,
    notification_type: str,
    title: str,
    body: str = '',
    link_path: str = '',
    team=None,
    match=None,
    competition=None,
    payload=None,
) -> GeniusNotification:
    n = GeniusNotification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title[:120],
        body=(body or '')[:280],
        link_path=link_path[:200],
        team=team,
        match=match,
        competition=competition,
        payload=payload or {},
    )
    try:
        from core.push_events import push_genius
        push_genius(user, title, body, link_path)
    except Exception:
        pass
    return n


def notify_team_members(
    team: GeniusTeam,
    notification_type: str,
    title: str,
    body: str = '',
    link_path: str = '',
    match=None,
    competition=None,
    payload=None,
    exclude_user_id: Optional[int] = None,
):
    for m in team.memberships.filter(status='active').select_related('user'):
        if exclude_user_id and m.user_id == exclude_user_id:
            continue
        create_notification(
            m.user,
            notification_type,
            title,
            body,
            link_path=link_path,
            team=team,
            match=match,
            competition=competition,
            payload=payload,
        )


def notify_user_invitation(to_user, team: GeniusTeam, from_user):
    name = from_user.first_name or from_user.username
    create_notification(
        to_user,
        'invite',
        f'Invitation — {team.name}',
        f'{name} t\'invite à rejoindre son équipe.',
        link_path=_dashboard_path('genius_hub'),
        team=team,
        payload={'team_id': team.id},
    )


def notify_join_request(team: GeniusTeam, from_user, request):
    name = from_user.first_name or from_user.username
    create_notification(
        team.captain,
        'join_request',
        f'Demande — {team.name}',
        f'{name} souhaite rejoindre ton équipe.',
        link_path=_dashboard_path('genius_hub'),
        team=team,
        payload={'request_id': request.id, 'from_user_id': from_user.id},
    )


def notify_join_request_accepted(to_user, team: GeniusTeam):
    create_notification(
        to_user,
        'join_request_accepted',
        f'Accepté — {team.name}',
        'Tu es maintenant membre de l\'équipe.',
        link_path=_dashboard_path('genius_hub'),
        team=team,
        payload={'team_id': team.id},
    )


def notify_captain_transfer(team: GeniusTeam, old_captain, new_captain):
    old_name = old_captain.first_name or old_captain.username
    new_name = new_captain.first_name or new_captain.username
    create_notification(
        new_captain,
        'captain_transfer',
        f'Capitaine — {team.name}',
        f'{old_name} t\'a nommé capitaine de l\'équipe.',
        link_path=_dashboard_path('genius_hub'),
        team=team,
        payload={'team_id': team.id},
    )
    notify_team_members(
        team,
        'captain_transfer',
        f'Nouveau capitaine — {team.name}',
        f'{new_name} est maintenant capitaine.',
        link_path=_dashboard_path('genius_hub'),
        team=team,
        exclude_user_id=new_captain.id,
    )


def notify_challenge_received(opponent_team: GeniusTeam, challenger_team: GeniusTeam, match: GeniusMatch):
    cap = opponent_team.captain
    create_notification(
        cap,
        'challenge',
        f'Défi de {challenger_team.name}',
        'Un match amical t\'attend — confirme ta présence.',
        link_path=_dashboard_path('genius_match', match.id),
        team=opponent_team,
        match=match,
        payload={'challenger': challenger_team.name},
    )


def notify_match_ready(match: GeniusMatch):
    path = _dashboard_path('genius_match', match.id)
    label = f'{match.team_a.name} vs {match.team_b.name}'
    for team in (match.team_a, match.team_b):
        notify_team_members(
            team,
            'match_ready',
            'Match à jouer',
            label,
            link_path=path,
            match=match,
            competition=match.competition,
        )


def notify_match_result(match: GeniusMatch):
    path = _dashboard_path('genius_match', match.id)
    if match.status == 'forfeit':
        body = f'Forfait — {match.forfeit_reason or "absence"}'
    else:
        body = f'Score final : {match.team_a_score} - {match.team_b_score}'
    for team in (match.team_a, match.team_b):
        won = match.winner_team_id == team.id
        title = 'Victoire !' if won else ('Défaite' if match.winner_team_id else 'Match terminé')
        notify_team_members(
            team,
            'match_result',
            title,
            body,
            link_path=path,
            match=match,
            competition=match.competition,
            payload={'won': won, 'score_a': match.team_a_score, 'score_b': match.team_b_score},
        )


def notify_competition_started(comp: GeniusCompetition):
    path = _dashboard_path('genius_competition', comp.id)
    for reg in comp.registrations.filter(status='roster_locked').select_related('team'):
        notify_team_members(
            reg.team,
            'competition_start',
            f'{comp.name} — c\'est parti !',
            'Le bracket est généré. Consulte tes matchs.',
            link_path=path,
            competition=comp,
        )


@transaction.atomic
def update_team_stats_from_match(match: GeniusMatch):
    if match.status not in ('finished', 'forfeit'):
        return
    for team, score, opp_score in (
        (match.team_a, match.team_a_score, match.team_b_score),
        (match.team_b, match.team_b_score, match.team_a_score),
    ):
        stats = _get_or_create_stats(team)
        stats.matches_played += 1
        stats.total_score += score
        if match.winner_team_id == team.id:
            stats.wins += 1
            stats.ranking_points += WIN_POINTS
            stats.team_streak += 1
            stats.team_xp += WIN_XP_BASE + stats.team_streak * STREAK_XP_BONUS
        elif match.winner_team_id and match.winner_team_id != team.id:
            stats.losses += 1
            stats.team_streak = 0
        else:
            stats.draws += 1
            stats.ranking_points += DRAW_POINTS
            stats.team_xp += 10
            stats.team_streak = 0
        stats.save()


def notifications_payload(user, limit: int = 20) -> List[dict]:
    rows = []
    for n in GeniusNotification.objects.filter(user=user).order_by('-created_at')[:limit]:
        rows.append({
            'id': n.id,
            'type': n.notification_type,
            'title': n.title,
            'body': n.body,
            'link_path': n.link_path,
            'read': n.read_at is not None,
            'created_at': n.created_at.isoformat(),
        })
    return rows


def unread_notification_count(user) -> int:
    return GeniusNotification.objects.filter(user=user, read_at__isnull=True).count()


@transaction.atomic
def mark_notifications_read(user, notification_ids: Optional[List[int]] = None):
    qs = GeniusNotification.objects.filter(user=user, read_at__isnull=True)
    if notification_ids:
        qs = qs.filter(pk__in=notification_ids)
    now = timezone.now()
    qs.update(read_at=now)
    return qs.count()


def leaderboard_payload(competition_id: Optional[int] = None, limit: int = 20) -> List[dict]:
    if competition_id:
        team_ids = GeniusRegistration.objects.filter(
            competition_id=competition_id,
            status__in=['registered', 'roster_locked', 'eliminated'],
        ).values_list('team_id', flat=True)
        stats_qs = GeniusTeamStats.objects.filter(team_id__in=team_ids).select_related('team')
    else:
        stats_qs = GeniusTeamStats.objects.filter(matches_played__gt=0).select_related('team')

    stats_qs = stats_qs.order_by('-ranking_points', '-wins', '-total_score')[:limit]
    rows = []
    rank = 1
    for s in stats_qs:
        rows.append({
            'rank': rank,
            'team_id': s.team_id,
            'name': s.team.name,
            'emblem': s.team.emblem_emoji,
            'wins': s.wins,
            'losses': s.losses,
            'draws': s.draws,
            'points': s.ranking_points,
            'matches': s.matches_played,
            'total_score': s.total_score,
        })
        rank += 1
    return rows


def team_stats_payload(team: GeniusTeam) -> dict:
    stats = _get_or_create_stats(team)
    return {
        'wins': stats.wins,
        'losses': stats.losses,
        'draws': stats.draws,
        'ranking_points': stats.ranking_points,
        'matches_played': stats.matches_played,
        'total_score': stats.total_score,
        'team_streak': stats.team_streak,
        'team_xp': stats.team_xp,
    }


def notify_match_reminder(user, match: GeniusMatch):
    create_notification(
        user,
        'match_reminder',
        'Match dans ~15 min',
        f'{match.team_a.name} vs {match.team_b.name}',
        link_path=_dashboard_path('genius_match', match.id),
        match=match,
    )


def send_scheduled_match_reminders() -> int:
    from datetime import timedelta

    now = timezone.now()
    window_start = now + timedelta(minutes=14)
    window_end = now + timedelta(minutes=16)
    matches = GeniusMatch.objects.filter(
        scheduled_at__gte=window_start,
        scheduled_at__lte=window_end,
        status__in=['scheduled', 'lobby'],
    )
    sent = 0
    for m in matches.select_related('team_a', 'team_b'):
        cfg = m.get_config() or {}
        if cfg.get('reminder_sent'):
            continue
        for mp in m.players.select_related('user'):
            notify_match_reminder(mp.user, m)
            sent += 1
        cfg['reminder_sent'] = True
        m.config = cfg
        m.save(update_fields=['config'])
    return sent


def upcoming_matches_for_user(user, limit: int = 5) -> List[dict]:
    match_ids = GeniusMatch.objects.filter(
        players__user=user,
        status__in=['scheduled', 'lobby'],
    ).values_list('pk', flat=True).distinct()
    rows = []
    for m in GeniusMatch.objects.filter(pk__in=match_ids).select_related('team_a', 'team_b', 'competition')[:limit]:
        rows.append({
            'match_id': m.id,
            'team_a': m.team_a.name,
            'team_b': m.team_b.name,
            'status': m.status,
            'competition': m.competition.name if m.competition_id else 'Match amical',
            'scheduled_at': m.scheduled_at.isoformat() if m.scheduled_at else None,
            'link_path': _dashboard_path('genius_match', m.id),
        })
    return rows


def post_match_study_tips(match: GeniusMatch, limit: int = 5) -> List[dict]:
    if match.status not in ('finished', 'forfeit'):
        return []
    tips = []
    seen = set()
    for q in match.questions.filter(is_correct=False).order_by('index'):
        if q.is_correct:
            continue
        qd = q.question_data or {}
        subj = q.subject or qd.get('subject') or ''
        topic = qd.get('category') or qd.get('theme') or ''
        key = (subj, topic)
        if key in seen or not subj:
            continue
        seen.add(key)
        tips.append({
            'subject': subj,
            'topic': topic,
            'label': topic or subj,
            'quiz_url': f'/dashboard/quiz/?subject={subj}',
            'plan_url': '/dashboard/plan/',
        })
        if len(tips) >= limit:
            break
    if not tips and match.questions.exists():
        subj = match.questions.first().subject or 'maths'
        tips.append({
            'subject': subj,
            'topic': '',
            'label': 'Révision générale',
            'quiz_url': f'/dashboard/quiz/?subject={subj}',
            'plan_url': '/dashboard/plan/',
        })
    return tips


def prizes_payload(comp: GeniusCompetition) -> List[dict]:
    from .constants import PRIZE_PLACE_META

    raw = comp.prizes or {}
    if isinstance(raw, list):
        rows = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get('place') or item.get('key') or '').lower()
            emoji, label = PRIZE_PLACE_META.get(key, ('🏆', item.get('label') or item.get('place') or 'Prix'))
            rows.append({
                'place': item.get('emoji') or emoji,
                'label': item.get('label') if item.get('label') and key not in PRIZE_PLACE_META else label,
                'reward': item.get('reward') or item.get('value') or '',
            })
        return rows
    if isinstance(raw, dict):
        rows = []
        order = {k: i for i, k in enumerate(PRIZE_PLACE_META)}
        for key, val in sorted(raw.items(), key=lambda kv: order.get(str(kv[0]).lower(), 99)):
            key_l = str(key).lower()
            emoji, label = PRIZE_PLACE_META.get(key_l, ('🏆', str(key).replace('_', ' ').title()))
            if isinstance(val, dict):
                place_key = str(val.get('place') or key_l).lower()
                emoji, mapped = PRIZE_PLACE_META.get(place_key, (emoji, label))
                raw_label = (val.get('label') or '').strip()
                if raw_label.lower() in PRIZE_PLACE_META or raw_label.lower() in ('champion', 'finalist'):
                    raw_label = mapped
                rows.append({
                    'place': val.get('emoji') or emoji,
                    'label': raw_label or mapped,
                    'reward': val.get('reward') or val.get('value') or '',
                })
            else:
                rows.append({'place': emoji, 'label': label, 'reward': str(val)})
        return rows
    return []
