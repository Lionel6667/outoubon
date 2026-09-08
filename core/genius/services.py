"""
Services métier — Groupes de Génies (validation serveur).
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.resource_index import get_targeted_questions, get_quiz_categories

from .constants import (
    ABSENCE_THRESHOLD_FOR_REMOVAL,
    MAX_TEAM_SIZE,
    MIN_PLAYERS_REQUIRED,
    MIN_PLAYERS_TO_START,
)
from .models import (
    GeniusBracketNode,
    GeniusCompetition,
    GeniusInvitation,
    GeniusJoinRequest,
    GeniusMatch,
    GeniusMatchEvent,
    GeniusMatchPlayer,
    GeniusMatchQuestion,
    GeniusMembership,
    GeniusRegistration,
    GeniusTeam,
    GeniusVote,
)
from .notifications import (
    notify_challenge_received,
    notify_competition_started,
    notify_join_request,
    notify_join_request_accepted,
    notify_captain_transfer,
    notify_match_ready,
    notify_match_result,
    notify_user_invitation,
    post_match_study_tips,
    update_team_stats_from_match,
)

User = get_user_model()


class GeniusError(Exception):
    def __init__(self, message: str, code: str = 'error'):
        self.message = message
        self.code = code
        super().__init__(message)


def _log_event(match: GeniusMatch, event_type: str, user=None, team=None, payload=None):
    GeniusMatchEvent.objects.create(
        match=match,
        event_type=event_type,
        user=user,
        team=team,
        payload=payload or {},
    )


def get_user_active_membership(user) -> Optional[GeniusMembership]:
    return GeniusMembership.objects.filter(user=user, status='active').select_related('team').first()


def get_user_active_team(user) -> Optional[GeniusTeam]:
    m = get_user_active_membership(user)
    return m.team if m else None


def team_members_payload(team: GeniusTeam) -> List[dict]:
    rows = []
    for m in team.memberships.filter(status='active').select_related('user'):
        u = m.user
        profile = getattr(u, 'profile', None)
        rows.append({
            'user_id': u.id,
            'username': u.username,
            'display_name': (u.first_name or u.username),
            'role': m.role,
            'is_captain': m.role == 'captain',
            'consecutive_missed': m.consecutive_missed,
            'removal_eligible': m.removal_eligible,
            'captain_eligible': m.captain_eligible,
            'avatar': profile.avatar.url if profile and profile.avatar else None,
        })
    return rows


@transaction.atomic
def create_team(
    user,
    name: str,
    description: str = '',
    emblem: str = '🧠',
    is_open_for_requests: bool = True,
) -> GeniusTeam:
    if get_user_active_team(user):
        raise GeniusError('Tu es déjà dans une équipe active.', 'already_in_team')
    name = (name or '').strip()[:80]
    if len(name) < 2:
        raise GeniusError('Nom d\'équipe invalide.', 'invalid_name')
    team = GeniusTeam.objects.create(
        name=name,
        description=(description or '')[:240],
        emblem_emoji=(emblem or '🧠')[:8],
        invite_code=GeniusTeam.generate_invite_code(),
        captain=user,
        is_open_for_requests=bool(is_open_for_requests),
    )
    GeniusMembership.objects.create(team=team, user=user, role='captain', status='active')
    return team


@transaction.atomic
def join_team_with_code(user, code: str) -> GeniusTeam:
    code = (code or '').strip().upper().replace(' ', '')
    if not code:
        raise GeniusError('Code requis.', 'invalid_code')
    if get_user_active_team(user):
        raise GeniusError('Tu es déjà dans une équipe.', 'already_in_team')
    try:
        team = GeniusTeam.objects.get(invite_code=code, is_active=True)
    except GeniusTeam.DoesNotExist:
        raise GeniusError('Code invalide.', 'not_found')
    if team.is_full():
        raise GeniusError('Cette équipe est complète (4/4).', 'team_full')
    if GeniusMembership.objects.filter(team=team, user=user, status='active').exists():
        return team
    GeniusMembership.objects.create(team=team, user=user, role='member', status='active')
    return team


def list_discoverable_teams(user) -> List[dict]:
    if get_user_active_team(user):
        return []
    from django.db.models import Count
    pending_ids = set(
        GeniusJoinRequest.objects.filter(from_user=user, status='pending').values_list('team_id', flat=True)
    )
    qs = (
        GeniusTeam.objects.filter(is_active=True, is_open_for_requests=True)
        .select_related('captain')
        .annotate(active_members=Count('memberships', filter=Q(memberships__status='active')))
        .order_by('-updated_at')[:24]
    )
    rows = []
    for team in qs:
        count = int(team.active_members or 0)
        if count >= MAX_TEAM_SIZE:
            continue
        cap = team.captain
        rows.append({
            'id': team.id,
            'name': team.name,
            'emblem': team.emblem_emoji,
            'member_count': count,
            'max_size': MAX_TEAM_SIZE,
            'captain_name': (cap.first_name or cap.username) if cap else '',
            'description': (team.description or '')[:120],
            'pending_request': team.id in pending_ids,
        })
    return rows


@transaction.atomic
def request_join_team(user, team_id: int, message: str = '') -> GeniusJoinRequest:
    if get_user_active_team(user):
        raise GeniusError('Tu es déjà dans une équipe.', 'already_in_team')
    try:
        team = GeniusTeam.objects.get(pk=team_id, is_active=True)
    except GeniusTeam.DoesNotExist:
        raise GeniusError('Équipe introuvable.', 'not_found')
    if team.is_full():
        raise GeniusError('Cette équipe est complète.', 'team_full')
    if not team.is_open_for_requests:
        raise GeniusError('Cette équipe n\'accepte pas les demandes.', 'not_open')
    if GeniusMembership.objects.filter(team=team, user=user, status='active').exists():
        raise GeniusError('Tu es déjà membre.', 'already_member')
    existing = GeniusJoinRequest.objects.filter(team=team, from_user=user, status='pending').first()
    if existing:
        return existing
    req = GeniusJoinRequest.objects.create(
        team=team,
        from_user=user,
        message=(message or '')[:200],
        status='pending',
    )
    notify_join_request(team, user, req)
    return req


@transaction.atomic
def respond_join_request(captain_user, request_id: int, accept: bool) -> dict:
    cap = GeniusMembership.objects.filter(
        user=captain_user, role='captain', status='active',
    ).select_related('team').first()
    if not cap:
        raise GeniusError('Seul le capitaine peut répondre.', 'not_captain')
    try:
        req = GeniusJoinRequest.objects.select_related('team', 'from_user').get(
            pk=request_id, team=cap.team,
        )
    except GeniusJoinRequest.DoesNotExist:
        raise GeniusError('Demande introuvable.', 'not_found')
    if req.status != 'pending':
        raise GeniusError('Demande déjà traitée.', 'invalid_state')
    if accept:
        if get_user_active_team(req.from_user):
            req.status = 'rejected'
            req.responded_at = timezone.now()
            req.save(update_fields=['status', 'responded_at'])
            raise GeniusError('Ce joueur est déjà dans une équipe.', 'target_in_team')
        if req.team.is_full():
            req.status = 'rejected'
            req.responded_at = timezone.now()
            req.save(update_fields=['status', 'responded_at'])
            raise GeniusError('Équipe complète.', 'team_full')
        GeniusMembership.objects.create(team=req.team, user=req.from_user, role='member', status='active')
        req.status = 'accepted'
        notify_join_request_accepted(req.from_user, req.team)
    else:
        req.status = 'rejected'
    req.responded_at = timezone.now()
    req.save(update_fields=['status', 'responded_at'])
    return {'ok': True, 'accepted': accept, 'team_id': req.team_id if accept else None}


@transaction.atomic
def transfer_captain(captain_user, team_id: int, new_captain_user_id: int) -> dict:
    cap_mem = GeniusMembership.objects.filter(
        user=captain_user, team_id=team_id, role='captain', status='active',
    ).select_related('team').first()
    if not cap_mem:
        raise GeniusError('Seul le capitaine peut transférer le rôle.', 'not_captain')
    if captain_user.id == new_captain_user_id:
        raise GeniusError('Choisis un autre membre.', 'invalid')
    new_mem = GeniusMembership.objects.filter(
        team_id=team_id, user_id=new_captain_user_id, status='active',
    ).first()
    if not new_mem:
        raise GeniusError('Membre introuvable.', 'not_found')
    if not new_mem.captain_eligible:
        raise GeniusError('Ce membre ne peut pas devenir capitaine.', 'not_eligible')
    team = cap_mem.team
    cap_mem.role = 'member'
    cap_mem.captain_eligible = False
    cap_mem.save(update_fields=['role', 'captain_eligible'])
    new_mem.role = 'captain'
    new_mem.save(update_fields=['role'])
    team.captain_id = new_captain_user_id
    team.save(update_fields=['captain', 'updated_at'])
    new_user = User.objects.get(pk=new_captain_user_id)
    notify_captain_transfer(team, captain_user, new_user)
    return {'ok': True, 'new_captain_id': new_captain_user_id}


@transaction.atomic
def set_team_open_for_requests(captain_user, team_id: int, open_requests: bool) -> GeniusTeam:
    cap = GeniusMembership.objects.filter(
        user=captain_user, team_id=team_id, role='captain', status='active',
    ).first()
    if not cap:
        raise GeniusError('Seul le capitaine peut modifier ce paramètre.', 'not_captain')
    team = cap.team
    team.is_open_for_requests = bool(open_requests)
    team.save(update_fields=['is_open_for_requests', 'updated_at'])
    return team


@transaction.atomic
def invite_user(captain_user, team_id: int, to_user_id: int) -> GeniusInvitation:
    membership = GeniusMembership.objects.filter(
        user=captain_user, team_id=team_id, role='captain', status='active',
    ).first()
    if not membership:
        raise GeniusError('Seul le capitaine peut inviter.', 'not_captain')
    team = membership.team
    if team.is_full():
        raise GeniusError('Équipe complète.', 'team_full')
    if get_user_active_team_id(to_user_id):
        raise GeniusError('Cet utilisateur est déjà dans une équipe.', 'target_in_team')

    inv = GeniusInvitation.objects.filter(
        team=team, to_user_id=to_user_id, status='pending',
    ).first()
    if inv:
        return inv
    inv = GeniusInvitation.objects.create(
        team=team, from_user=captain_user, to_user_id=to_user_id,
        status='pending',
        expires_at=timezone.now() + timedelta(days=7),
    )
    notify_user_invitation(User.objects.get(pk=to_user_id), team, captain_user)
    return inv


def get_user_active_team_id(user_id: int) -> Optional[int]:
    m = GeniusMembership.objects.filter(user_id=user_id, status='active').first()
    return m.team_id if m else None


@transaction.atomic
def respond_invitation(user, invitation_id: int, accept: bool) -> dict:
    try:
        inv = GeniusInvitation.objects.select_related('team').get(id=invitation_id, to_user=user)
    except GeniusInvitation.DoesNotExist:
        raise GeniusError('Invitation introuvable.', 'not_found')
    if inv.status != 'pending':
        raise GeniusError('Invitation déjà traitée.', 'invalid_state')
    if inv.is_expired():
        inv.status = 'expired'
        inv.save(update_fields=['status'])
        raise GeniusError('Invitation expirée.', 'expired')
    if accept:
        if get_user_active_team(user):
            raise GeniusError('Tu es déjà dans une équipe.', 'already_in_team')
        if inv.team.is_full():
            raise GeniusError('Équipe complète.', 'team_full')
        GeniusMembership.objects.create(team=inv.team, user=user, role='member', status='active')
        inv.status = 'accepted'
    else:
        inv.status = 'rejected'
    inv.responded_at = timezone.now()
    inv.save(update_fields=['status', 'responded_at'])
    return {'ok': True, 'team_id': inv.team_id if accept else None}


@transaction.atomic
def request_remove_member(captain_user, team_id: int, member_user_id: int) -> dict:
    if captain_user.id == member_user_id:
        raise GeniusError('Le capitaine ne peut pas se retirer ainsi.', 'invalid')
    cap = GeniusMembership.objects.filter(
        user=captain_user, team_id=team_id, role='captain', status='active',
    ).first()
    if not cap:
        raise GeniusError('Seul le capitaine peut demander un retrait.', 'not_captain')
    mem = GeniusMembership.objects.filter(
        team_id=team_id, user_id=member_user_id, status='active',
    ).first()
    if not mem:
        raise GeniusError('Membre introuvable.', 'not_found')
    if mem.role == 'captain':
        raise GeniusError('Impossible de retirer le capitaine.', 'captain')
    if not mem.removal_eligible:
        raise GeniusError(
            'Retrait impossible : le joueur doit manquer 2 matchs consécutifs.',
            'not_eligible',
        )
    mem.status = 'removed'
    mem.left_at = timezone.now()
    mem.save(update_fields=['status', 'left_at'])
    return {'ok': True}


@transaction.atomic
def register_team_for_competition(captain_user, competition_id: int, team_id: int) -> GeniusRegistration:
    cap = GeniusMembership.objects.filter(
        user=captain_user, team_id=team_id, role='captain', status='active',
    ).first()
    if not cap:
        raise GeniusError('Seul le capitaine peut inscrire l\'équipe.', 'not_captain')
    try:
        comp = GeniusCompetition.objects.get(pk=competition_id)
    except GeniusCompetition.DoesNotExist:
        raise GeniusError('Concours introuvable.', 'not_found')
    is_weekly = bool((comp.config or {}).get('weekly')) or (comp.config or {}).get('slug') == 'weekly-general'
    if is_weekly or (comp.config or {}).get('week_key'):
        from .schedule import is_registration_open
        if not is_registration_open():
            raise GeniusError(
                'Inscriptions au concours général : dimanche uniquement. Lundi c\'est fermé.',
                'registration_sunday_only',
            )
        if comp.status != 'registration':
            raise GeniusError('Inscriptions fermées.', 'closed')
    elif comp.status not in ('registration', 'draft'):
        raise GeniusError('Inscriptions fermées.', 'closed')
    if team_id and cap.team.member_count() < MAX_TEAM_SIZE:
        raise GeniusError(
            f'Équipe incomplète ({cap.team.member_count()}/{MAX_TEAM_SIZE}). Recrute tes coéquipiers.',
            'team_incomplete',
        )
    reg, created = GeniusRegistration.objects.get_or_create(
        competition=comp,
        team_id=team_id,
        defaults={'status': 'registered'},
    )
    if not created and reg.status == 'withdrawn':
        reg.status = 'registered'
        reg.save(update_fields=['status'])
    return reg


@transaction.atomic
def lock_roster(captain_user, competition_id: int, team_id: int) -> GeniusRegistration:
    cap = GeniusMembership.objects.filter(
        user=captain_user, team_id=team_id, role='captain', status='active',
    ).first()
    if not cap:
        raise GeniusError('Seul le capitaine peut verrouiller le roster.', 'not_captain')
    if cap.team.member_count() < MAX_TEAM_SIZE:
        raise GeniusError('Roster incomplet : 4 joueurs requis.', 'team_incomplete')
    try:
        reg = GeniusRegistration.objects.get(competition_id=competition_id, team_id=team_id)
    except GeniusRegistration.DoesNotExist:
        raise GeniusError('Équipe non inscrite.', 'not_registered')
    reg.status = 'roster_locked'
    reg.roster_locked_at = timezone.now()
    reg.save(update_fields=['status', 'roster_locked_at'])
    return reg


def _normalize_question(q: dict) -> dict:
    opts = q.get('options') or []
    correct = q.get('correct') or q.get('reponse_correcte') or ''
    if isinstance(correct, int) and opts:
        letters = ['A', 'B', 'C', 'D']
        correct = letters[correct] if 0 <= correct < len(letters) else str(correct)
    correct = str(correct).strip().upper()[:1]
    return {
        'id': q.get('id', ''),
        'question': q.get('question') or q.get('enonce') or '',
        'options': opts[:4],
        'correct': correct,
        'explanation': q.get('explanation') or q.get('explication') or '',
        'category': q.get('category') or q.get('theme') or '',
        'subject': q.get('subject') or '',
    }


def _weak_topics_for_match(match: GeniusMatch) -> Dict[str, List[str]]:
    """Agrège les faiblesses (SubjectMastery) des joueurs du match."""
    from core.models import SubjectMastery

    user_ids = list(match.players.values_list('user_id', flat=True))
    if not user_ids:
        return {}
    weak_by_subject: Dict[str, List[tuple]] = {}
    for sm in SubjectMastery.objects.filter(user_id__in=user_ids):
        subj = (sm.subject or '').strip()
        if not subj:
            continue
        score = float(sm.mastery_score or 0)
        topics = sm.weak_topics if isinstance(sm.weak_topics, list) else []
        if topics:
            for t in topics[:6]:
                t = str(t).strip()
                if t:
                    weak_by_subject.setdefault(subj, []).append((score, t))
        else:
            weak_by_subject.setdefault(subj, []).append((score, ''))
    result: Dict[str, List[str]] = {}
    for subj, entries in weak_by_subject.items():
        entries.sort(key=lambda x: x[0])
        seen = set()
        topics = []
        for _, t in entries:
            key = t or subj
            if key in seen:
                continue
            seen.add(key)
            topics.append(t)
        result[subj] = topics[:5]
    return result


def _pick_questions_for_match(match: GeniusMatch) -> List[dict]:
    cfg = match.get_config()
    n = int(cfg.get('question_count', 10))
    subjects_a = _team_subjects(match.team_a)
    subjects_b = _team_subjects(match.team_b)
    common = list(set(subjects_a) & set(subjects_b))
    pool_subj = common or list(set(subjects_a) | set(subjects_b))
    if not pool_subj:
        pool_subj = ['maths']
    weakness_map = _weak_topics_for_match(match)
    questions = []
    for i in range(n):
        subj = pool_subj[i % len(pool_subj)]
        weak_topics = weakness_map.get(subj) or []
        topic = weak_topics[i % len(weak_topics)] if weak_topics else ''
        if not topic:
            cats = get_quiz_categories(subj) or []
            topic = cats[i % len(cats)] if cats else ''
        batch = get_targeted_questions(subj, [topic] if topic else [], n=1)
        if not batch:
            batch = get_targeted_questions(subj, [], n=1)
        if batch:
            qd = _normalize_question(batch[0])
            if not qd.get('subject'):
                qd['subject'] = subj
            if topic and not qd.get('category'):
                qd['category'] = topic
            questions.append(qd)
        else:
            questions.append({
                'id': f'placeholder_{i}',
                'question': f'Question {subj} — {topic or "révision"}',
                'options': ['A', 'B', 'C', 'D'],
                'correct': 'A',
                'explanation': 'Révision recommandée sur ce thème.',
                'category': topic,
                'subject': subj,
            })
    return questions


def _team_subjects(team: GeniusTeam) -> List[str]:
    from core.views import _get_user_serie_subjects
    subs = set()
    for m in team.memberships.filter(status='active').select_related('user'):
        subs.update(_get_user_serie_subjects(m.user) or [])
    return list(subs)


@transaction.atomic
def create_match(competition_id: Optional[int], team_a_id: int, team_b_id: int) -> GeniusMatch:
    if team_a_id == team_b_id:
        raise GeniusError('Les deux équipes doivent être différentes.', 'invalid')
    match = GeniusMatch.objects.create(
        competition_id=competition_id,
        team_a_id=team_a_id,
        team_b_id=team_b_id,
        status='scheduled',
        phase='waiting',
    )
    for team in (match.team_a, match.team_b):
        for m in team.memberships.filter(status='active'):
            GeniusMatchPlayer.objects.create(match=match, user=m.user, team=team, present=False)
    notify_match_ready(match)
    return match


@transaction.atomic
def mark_presence(user, match_id: int, present: bool = True) -> dict:
    try:
        mp = GeniusMatchPlayer.objects.select_related('match', 'team').get(match_id=match_id, user=user)
    except GeniusMatchPlayer.DoesNotExist:
        raise GeniusError('Tu ne participes pas à ce match.', 'not_in_match')
    match = mp.match
    if match.status in ('finished', 'forfeit', 'cancelled'):
        raise GeniusError('Match terminé.', 'finished')
    mp.present = present
    mp.marked_at = timezone.now()
    mp.save(update_fields=['present', 'marked_at'])
    _log_event(match, 'player_presence', user=user, team=mp.team, payload={'present': present})
    return {'ok': True, 'present': present}


def _min_present_required(team: GeniusTeam) -> int:
    active = team.member_count()
    if active >= MAX_TEAM_SIZE:
        return MIN_PLAYERS_TO_START
    return max(MIN_PLAYERS_REQUIRED, active)


def _count_present(match: GeniusMatch, team: GeniusTeam) -> Tuple[int, bool]:
    players = match.players.filter(team=team)
    total = players.count()
    present = players.filter(present=True).count()
    cap_present = players.filter(present=True, user_id=team.captain_id).exists()
    return present, cap_present


def _check_can_start(match: GeniusMatch) -> Optional[str]:
    pa, cap_a = _count_present(match, match.team_a)
    pb, cap_b = _count_present(match, match.team_b)
    if not cap_a:
        return 'forfeit_a_captain'
    if not cap_b:
        return 'forfeit_b_captain'
    min_a = _min_present_required(match.team_a)
    min_b = _min_present_required(match.team_b)
    if pa < min_a or pb < min_b:
        return 'insufficient_players'
    return None


@transaction.atomic
def start_match(user, match_id: int) -> GeniusMatch:
    try:
        match = GeniusMatch.objects.select_related('team_a', 'team_b').get(pk=match_id)
    except GeniusMatch.DoesNotExist:
        raise GeniusError('Match introuvable.', 'not_found')
    mem = GeniusMembership.objects.filter(
        user=user, status='active', team_id__in=[match.team_a_id, match.team_b_id],
    ).first()
    if not mem or mem.role != 'captain':
        raise GeniusError('Seul un capitaine peut lancer le match.', 'not_captain')
    if match.status not in ('scheduled', 'lobby'):
        raise GeniusError('Match déjà commencé.', 'invalid_state')

    issue = _check_can_start(match)
    if issue == 'forfeit_a_captain':
        _apply_forfeit(match, match.team_a, 'captain_absent')
        return match
    if issue == 'forfeit_b_captain':
        _apply_forfeit(match, match.team_b, 'captain_absent')
        return match
    if issue == 'insufficient_players':
        min_a = _min_present_required(match.team_a)
        min_b = _min_present_required(match.team_b)
        raise GeniusError(
            f'Présence insuffisante (min {min_a}/{match.team_a.member_count()} et '
            f'{min_b}/{match.team_b.member_count()} joueurs).',
            'insufficient',
        )

    raw_qs = _pick_questions_for_match(match)
    for i, qd in enumerate(raw_qs):
        responding = match.team_a if i % 2 == 0 else match.team_b
        GeniusMatchQuestion.objects.create(
            match=match,
            index=i,
            subject=qd.get('subject', ''),
            question_type='team',
            responding_team=responding,
            question_data=qd,
            correct_choice=qd.get('correct', 'A'),
        )
    match.status = 'in_progress'
    match.started_at = timezone.now()
    match.current_question_index = 0
    match.save(update_fields=['status', 'started_at', 'current_question_index'])
    _advance_to_question(match)
    _log_event(match, 'match_started', user=user)
    return match


def _apply_forfeit(match: GeniusMatch, forfeit_team: GeniusTeam, reason: str):
    if forfeit_team.id == match.team_a_id:
        winner = match.team_b
    else:
        winner = match.team_a
    match.status = 'forfeit'
    match.phase = 'finished'
    match.forfeit_team = forfeit_team
    match.forfeit_reason = reason
    match.winner_team = winner
    match.finished_at = timezone.now()
    match.save()
    _log_event(match, 'forfeit', team=forfeit_team, payload={'reason': reason})
    _update_absence_counters(match)
    update_team_stats_from_match(match)
    notify_match_result(match)


def _advance_to_question(match: GeniusMatch):
    cfg = match.get_config()
    qs = match.questions.filter(index=match.current_question_index).first()
    if not qs:
        _finish_match(match)
        return
    now = timezone.now()
    match.responding_team = qs.responding_team
    match.phase = 'answering'
    match.phase_deadline = now + timedelta(seconds=int(cfg.get('answer_seconds', 45)))
    match.save(update_fields=['responding_team', 'phase', 'phase_deadline', 'current_question_index'])
    qs.started_at = now
    qs.save(update_fields=['started_at'])
    _log_event(match, 'question_started', payload={'index': qs.index})


@transaction.atomic
def submit_vote(user, match_id: int, choice: str) -> dict:
    choice = (choice or '').strip().upper()[:1]
    if choice not in ('A', 'B', 'C', 'D'):
        raise GeniusError('Choix invalide.', 'invalid_choice')
    try:
        match = GeniusMatch.objects.get(pk=match_id)
    except GeniusMatch.DoesNotExist:
        raise GeniusError('Match introuvable.', 'not_found')
    if match.phase != 'answering':
        raise GeniusError('Pas en phase de réponse.', 'invalid_phase')
    mp = GeniusMatchPlayer.objects.filter(match=match, user=user, present=True).first()
    if not mp:
        raise GeniusError('Tu n\'es pas actif dans ce match.', 'not_active')
    if mp.team_id != match.responding_team_id:
        raise GeniusError('Ce n\'est pas le tour de ton équipe.', 'not_your_turn')
    qs = match.questions.filter(index=match.current_question_index).first()
    if not qs or qs.closed_at:
        raise GeniusError('Question fermée.', 'closed')
    if match.phase_deadline and timezone.now() > match.phase_deadline:
        raise GeniusError('Temps écoulé.', 'timeout')
    GeniusVote.objects.update_or_create(
        match_question=qs,
        user=user,
        defaults={'team_id': mp.team_id, 'choice': choice},
    )
    _log_event(match, 'vote_cast', user=user, team=mp.team, payload={'choice': choice})
    return {'ok': True}


def _apply_lock(match: GeniusMatch, qs: GeniusMatchQuestion, choice: str, user=None, team=None):
    choice = (choice or 'A').strip().upper()[:1]
    if qs.closed_at:
        return
    qs.final_choice = choice
    qs.is_correct = choice == qs.correct_choice
    cfg = match.get_config()
    pts = int(cfg.get('team_question_points', 100)) if qs.is_correct else 0
    qs.points_awarded = pts
    qs.closed_at = timezone.now()
    qs.save()
    if qs.is_correct:
        if match.responding_team_id == match.team_a_id:
            match.team_a_score += pts
        else:
            match.team_b_score += pts
    match.phase = 'revealing'
    match.phase_deadline = timezone.now() + timedelta(seconds=int(cfg.get('reveal_seconds', 5)))
    match.save(update_fields=['team_a_score', 'team_b_score', 'phase', 'phase_deadline'])
    _log_event(
        match, 'captain_locked', user=user, team=team,
        payload={'choice': choice, 'correct': qs.is_correct},
    )


@transaction.atomic
def captain_lock_answer(user, match_id: int, choice: str) -> dict:
    choice = (choice or '').strip().upper()[:1]
    if choice not in ('A', 'B', 'C', 'D'):
        raise GeniusError('Choix invalide.', 'invalid_choice')
    try:
        match = GeniusMatch.objects.select_related('team_a', 'team_b').get(pk=match_id)
    except GeniusMatch.DoesNotExist:
        raise GeniusError('Match introuvable.', 'not_found')
    if match.phase != 'answering':
        raise GeniusError('Pas en phase de réponse.', 'invalid_phase')
    mem = GeniusMembership.objects.filter(user=user, status='active').first()
    if not mem or mem.role != 'captain':
        raise GeniusError('Seul le capitaine peut verrouiller la réponse.', 'not_captain')
    if match.responding_team_id != mem.team_id:
        raise GeniusError('Ce n\'est pas le tour de ton équipe.', 'not_your_turn')
    qs = match.questions.filter(index=match.current_question_index).first()
    if not qs or qs.closed_at:
        raise GeniusError('Question fermée.', 'closed')
    _apply_lock(match, qs, choice, user=user, team=mem.team)
    qs.refresh_from_db()
    return {'ok': True, 'correct': qs.is_correct, 'points': qs.points_awarded}


@transaction.atomic
def advance_match_phase(match_id: int) -> GeniusMatch:
    """Appelé par polling quand deadline atteinte ou après révélation."""
    try:
        match = GeniusMatch.objects.select_related('team_a', 'team_b').get(pk=match_id)
    except GeniusMatch.DoesNotExist:
        raise GeniusError('Match introuvable.', 'not_found')
    if match.status != 'in_progress':
        return match
    now = timezone.now()
    cfg = match.get_config()
    qs = match.questions.filter(index=match.current_question_index).first()

    if (
        match.phase == 'answering'
        and match.phase_deadline
        and now >= match.phase_deadline
        and qs
        and not qs.closed_at
    ):
        votes = list(GeniusVote.objects.filter(match_question=qs).values_list('choice', flat=True))
        final = Counter(votes).most_common(1)[0][0] if votes else 'A'
        _apply_lock(match, qs, final)
        match.refresh_from_db()
        qs.refresh_from_db()

    if match.phase == 'revealing' and match.phase_deadline and now >= match.phase_deadline:
        qs.revealed = True
        qs.save(update_fields=['revealed'])
        match.phase = 'explanation'
        match.phase_deadline = now + timedelta(seconds=int(cfg.get('explanation_seconds', 10)))
        match.save(update_fields=['phase', 'phase_deadline'])
        _log_event(match, 'answer_revealed', payload={'index': qs.index})

    if match.phase == 'explanation' and match.phase_deadline and now >= match.phase_deadline:
        match.current_question_index += 1
        match.save(update_fields=['current_question_index'])
        _advance_to_question(match)

    return match


def _finish_match(match: GeniusMatch):
    match.status = 'finished'
    match.phase = 'finished'
    match.finished_at = timezone.now()
    if match.team_a_score > match.team_b_score:
        match.winner_team = match.team_a
    elif match.team_b_score > match.team_a_score:
        match.winner_team = match.team_b
    match.save()
    _log_event(match, 'match_finished')
    _update_absence_counters(match)
    update_team_stats_from_match(match)
    notify_match_result(match)
    try:
        node = match.bracket_node
        node.winner_team = match.winner_team
        node.save(update_fields=['winner_team'])
        _propagate_bracket_winner(node)
        if match.competition_id and node.phase_key == 'groups':
            maybe_build_knockout_from_groups(match.competition_id)
    except GeniusBracketNode.DoesNotExist:
        pass


def _update_absence_counters(match: GeniusMatch):
    for mp in match.players.select_related('user'):
        mem = GeniusMembership.objects.filter(
            team=mp.team, user=mp.user, status='active',
        ).first()
        if not mem:
            continue
        if mp.present:
            mem.consecutive_missed = 0
            mem.removal_eligible = False
        else:
            mem.consecutive_missed += 1
            if mem.consecutive_missed >= ABSENCE_THRESHOLD_FOR_REMOVAL:
                mem.removal_eligible = True
        mem.save(update_fields=['consecutive_missed', 'removal_eligible'])


def match_state_payload(match: GeniusMatch, for_user=None) -> dict:
    match = GeniusMatch.objects.select_related('team_a', 'team_b', 'winner_team').get(pk=match.pk)
    advance_match_phase(match.id)
    match.refresh_from_db()

    qs = match.questions.filter(index=match.current_question_index).first()
    votes_by_choice: Dict[str, List[dict]] = {'A': [], 'B': [], 'C': [], 'D': []}
    if qs:
        for v in qs.votes.select_related('user'):
            votes_by_choice.setdefault(v.choice, []).append({
                'user_id': v.user_id,
                'name': v.user.first_name or v.user.username,
            })

    players = []
    for mp in match.players.select_related('user', 'team'):
        players.append({
            'user_id': mp.user_id,
            'team_id': mp.team_id,
            'present': mp.present,
            'name': mp.user.first_name or mp.user.username,
        })

    question_payload = None
    if qs:
        qd = qs.question_data or {}
        question_payload = {
            'index': qs.index,
            'total': match.questions.count(),
            'subject': qs.subject,
            'question': qd.get('question', ''),
            'options': qd.get('options', []),
            'responding_team_id': qs.responding_team_id,
            'responding_team_name': qs.responding_team.name,
            'votes': votes_by_choice,
            'final_choice': qs.final_choice if qs.revealed or match.phase in ('revealing', 'explanation') else '',
            'correct_choice': qs.correct_choice if qs.revealed else '',
            'explanation': qd.get('explanation', '') if qs.revealed or match.phase == 'explanation' else '',
            'is_correct': qs.is_correct if qs.revealed else None,
            'points': qs.points_awarded if qs.revealed else 0,
        }

    deadline_ms = None
    if match.phase_deadline:
        deadline_ms = int(match.phase_deadline.timestamp() * 1000)

    return {
        'match_id': match.id,
        'status': match.status,
        'phase': match.phase,
        'scheduled_at': match.scheduled_at.isoformat() if match.scheduled_at else None,
        'team_a': {'id': match.team_a_id, 'name': match.team_a.name, 'score': match.team_a_score},
        'team_b': {'id': match.team_b_id, 'name': match.team_b.name, 'score': match.team_b_score},
        'players': players,
        'question': question_payload,
        'deadline_ms': deadline_ms,
        'server_now_ms': int(timezone.now().timestamp() * 1000),
        'winner_team_id': match.winner_team_id,
        'forfeit_reason': match.forfeit_reason or '',
        'study_tips': post_match_study_tips(match) if match.status in ('finished', 'forfeit') else [],
        'captain_lock_hint': _captain_lock_hint(match, qs),
        'lobby_summary': _lobby_summary(match),
    }


def _lobby_summary(match: GeniusMatch) -> dict:
    def team_block(team: GeniusTeam) -> dict:
        players = match.players.filter(team=team).select_related('user')
        present = [p for p in players if p.present]
        return {
            'team_id': team.id,
            'name': team.name,
            'present_count': len(present),
            'total': players.count(),
            'ready': [
                {'user_id': p.user_id, 'name': p.user.first_name or p.user.username}
                for p in present
            ],
        }
    return {
        'team_a': team_block(match.team_a),
        'team_b': team_block(match.team_b),
    }


def _captain_lock_hint(match: GeniusMatch, qs) -> str:
    if match.phase != 'answering' or not qs or not qs.responding_team_id:
        return ''
    team_id = qs.responding_team_id
    voters = GeniusVote.objects.filter(match_question=qs, team_id=team_id).count()
    total = match.players.filter(team_id=team_id).count()
    if voters >= total and total > 0:
        return 'Tous les joueurs ont voté — le capitaine peut verrouiller.'
    if voters > 0:
        return f'{voters}/{total} joueurs ont voté.'
    return ''


def bracket_payload(competition: GeniusCompetition) -> List[dict]:
    nodes = GeniusBracketNode.objects.filter(competition=competition).select_related(
        'team_a', 'team_b', 'winner_team', 'match',
    ).order_by('round_order', 'position')
    return [
        {
            'id': n.id,
            'phase_key': n.phase_key,
            'phase_label': n.phase_label,
            'round_order': n.round_order,
            'position': n.position,
            'team_a': n.team_a.name if n.team_a else None,
            'team_b': n.team_b.name if n.team_b else None,
            'winner': n.winner_team.name if n.winner_team else None,
            'match_id': n.match_id,
            'match_status': n.match.status if n.match else None,
        }
        for n in nodes
    ]


def list_challengeable_teams(exclude_team_id: Optional[int] = None) -> List[dict]:
    from django.db.models import Count
    qs = (
        GeniusTeam.objects.filter(is_active=True)
        .annotate(active_members=Count('memberships', filter=Q(memberships__status='active')))
        .filter(active_members__gte=MIN_PLAYERS_REQUIRED)
        .order_by('-updated_at')
    )
    if exclude_team_id:
        qs = qs.exclude(pk=exclude_team_id)
    rows = []
    for team in qs[:20]:
        rows.append({
            'id': team.id,
            'name': team.name,
            'emblem': team.emblem_emoji,
            'member_count': int(team.active_members or 0),
            'invite_code': team.invite_code,
        })
    return rows


@transaction.atomic
def create_friendly_match(captain_user, opponent_team_id: int) -> GeniusMatch:
    cap = GeniusMembership.objects.filter(
        user=captain_user, role='captain', status='active',
    ).select_related('team').first()
    if not cap:
        raise GeniusError('Seul le capitaine peut lancer un défi.', 'not_captain')
    my_team = cap.team
    if my_team.member_count() < MIN_PLAYERS_REQUIRED:
        raise GeniusError('Ton équipe doit avoir au moins 2 joueurs.', 'team_incomplete')
    if opponent_team_id == my_team.id:
        raise GeniusError('Tu ne peux pas défier ta propre équipe.', 'invalid')
    try:
        opponent = GeniusTeam.objects.get(pk=opponent_team_id, is_active=True)
    except GeniusTeam.DoesNotExist:
        raise GeniusError('Équipe adverse introuvable.', 'not_found')
    if opponent.member_count() < MIN_PLAYERS_REQUIRED:
        raise GeniusError('L\'équipe adverse n\'est pas prête.', 'opponent_incomplete')
    active = GeniusMatch.objects.filter(
        competition__isnull=True,
        status__in=['scheduled', 'lobby', 'in_progress'],
    ).filter(
        Q(team_a=my_team, team_b=opponent) | Q(team_a=opponent, team_b=my_team),
    ).exists()
    if active:
        raise GeniusError('Un match amical est déjà en cours entre ces équipes.', 'match_exists')
    match = create_match(None, my_team.id, opponent.id)
    notify_challenge_received(opponent, my_team, match)
    return match


@transaction.atomic
def create_friendly_match_by_code(captain_user, invite_code: str) -> GeniusMatch:
    code = (invite_code or '').strip().upper().replace(' ', '')
    try:
        opponent = GeniusTeam.objects.get(invite_code=code, is_active=True)
    except GeniusTeam.DoesNotExist:
        raise GeniusError('Code équipe invalide.', 'not_found')
    return create_friendly_match(captain_user, opponent.id)


@transaction.atomic
def schedule_match(user, match_id: int, scheduled_at_raw: str) -> GeniusMatch:
    mem = GeniusMembership.objects.filter(user=user, role='captain', status='active').first()
    if not mem:
        raise GeniusError('Seul le capitaine peut programmer un match.', 'not_captain')
    try:
        match = GeniusMatch.objects.select_related('team_a', 'team_b').get(pk=match_id)
    except GeniusMatch.DoesNotExist:
        raise GeniusError('Match introuvable.', 'not_found')
    if mem.team_id not in (match.team_a_id, match.team_b_id):
        raise GeniusError('Accès refusé.', 'forbidden')
    if match.status not in ('scheduled', 'lobby'):
        raise GeniusError('Match déjà commencé.', 'invalid_state')
    try:
        scheduled = datetime.fromisoformat(str(scheduled_at_raw).replace('Z', '+00:00'))
        if timezone.is_naive(scheduled):
            scheduled = timezone.make_aware(scheduled, timezone.get_current_timezone())
    except (ValueError, TypeError):
        raise GeniusError('Date/heure invalide.', 'invalid_date')
    if scheduled <= timezone.now():
        raise GeniusError('Choisis une date dans le futur.', 'invalid_date')
    # Concours : créneau 19h–20h obligatoire (matchs parallèles OK)
    if match.competition_id:
        from .schedule import validate_match_schedule
        ok, msg = validate_match_schedule(scheduled)
        if not ok:
            raise GeniusError(msg, 'outside_match_window')
    match.scheduled_at = scheduled
    cfg = match.get_config() or {}
    cfg['reminder_sent'] = False
    match.config = cfg
    match.save(update_fields=['scheduled_at', 'config'])
    return match


def team_match_history(team: GeniusTeam, limit: int = 15) -> List[dict]:
    qs = GeniusMatch.objects.filter(
        Q(team_a=team) | Q(team_b=team),
        status__in=['finished', 'forfeit'],
    ).select_related('team_a', 'team_b', 'winner_team', 'competition').order_by('-finished_at', '-id')[:limit]
    rows = []
    for m in qs:
        is_a = m.team_a_id == team.id
        opp = m.team_b if is_a else m.team_a
        won = m.winner_team_id == team.id
        rows.append({
            'id': m.id,
            'opponent': opp.name,
            'opponent_emblem': opp.emblem_emoji,
            'score_us': m.team_a_score if is_a else m.team_b_score,
            'score_them': m.team_b_score if is_a else m.team_a_score,
            'won': won,
            'forfeit': m.status == 'forfeit',
            'competition': m.competition.name if m.competition else 'Amical',
            'finished_at': m.finished_at.isoformat() if m.finished_at else None,
        })
    return rows


def team_activity_feed(team: GeniusTeam, limit: int = 12) -> List[dict]:
    feed = []
    for m in team_match_history(team, limit):
        feed.append({
            'type': 'match_result',
            'title': f"{'Victoire' if m['won'] else 'Défaite'} vs {m['opponent']}",
            'body': f"{m['score_us']}-{m['score_them']} · {m['competition']}",
            'at': m['finished_at'],
            'match_id': m['id'],
        })
    for node in GeniusBracketNode.objects.filter(
        Q(team_a=team) | Q(team_b=team),
        winner_team__isnull=False,
    ).select_related('competition', 'winner_team', 'match').order_by('-id')[:limit]:
        if node.winner_team_id != team.id and node.match and node.match.status in ('finished', 'forfeit'):
            feed.append({
                'type': 'elimination',
                'title': f'Élimination · {node.competition.name}',
                'body': f"Éliminé par {node.winner_team.name}",
                'at': node.match.finished_at.isoformat() if node.match.finished_at else None,
                'competition_id': node.competition_id,
            })
    feed.sort(key=lambda x: x.get('at') or '', reverse=True)
    return feed[:limit]


def _next_power_of_2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def _round_phase(comp: GeniusCompetition, round_order: int, final_round_order: int) -> Tuple[str, str]:
    phases = comp.get_config().get('phases') or []
    if phases:
        keys = [p.get('key', 'round') for p in phases]
        labels = [p.get('label', p.get('key', 'round')) for p in phases]
    else:
        keys = ['groups', 'quarters', 'semis', 'final']
        labels = ['Phase de groupes', 'Quarts de finale', 'Demi-finales', 'Finale']
    if round_order >= final_round_order:
        idx = len(keys) - 1
    else:
        idx = min(round_order, len(keys) - 1)
    return keys[idx], labels[idx]


def _build_group_stage(comp: GeniusCompetition, teams: List[GeniusTeam]) -> int:
    group_size = 4
    num_groups = max(2, (len(teams) + group_size - 1) // group_size)
    groups: List[List[GeniusTeam]] = []
    for g in range(num_groups):
        chunk = teams[g * group_size:(g + 1) * group_size]
        if chunk:
            groups.append(chunk)
    match_count = 0
    group_team_ids = []
    for g_idx, group_teams in enumerate(groups):
        group_team_ids.append([t.id for t in group_teams])
        pairs = []
        for i in range(len(group_teams)):
            for j in range(i + 1, len(group_teams)):
                pairs.append((group_teams[i], group_teams[j]))
        for p_idx, (ta, tb) in enumerate(pairs):
            node = GeniusBracketNode.objects.create(
                competition=comp,
                phase_key='groups',
                phase_label=f'Poule {g_idx + 1}',
                round_order=0,
                position=g_idx * 100 + p_idx,
                team_a=ta,
                team_b=tb,
            )
            node.match = create_match(comp.id, ta.id, tb.id)
            node.save(update_fields=['match'])
            match_count += 1
    cfg = comp.get_config() or {}
    cfg['group_stage'] = {
        'num_groups': len(groups),
        'group_team_ids': group_team_ids,
        'knockout_built': False,
    }
    comp.config = cfg
    comp.save(update_fields=['config'])
    return match_count


def _compute_group_standings(comp: GeniusCompetition) -> List[List[dict]]:
    cfg = comp.get_config().get('group_stage') or {}
    group_team_ids = cfg.get('group_team_ids') or []
    all_standings = []
    for g_idx, team_ids in enumerate(group_team_ids):
        table = {
            tid: {'team_id': tid, 'wins': 0, 'losses': 0, 'points': 0, 'scored': 0, 'against': 0}
            for tid in team_ids
        }
        nodes = comp.bracket_nodes.filter(
            round_order=0,
            position__gte=g_idx * 100,
            position__lt=(g_idx + 1) * 100,
        ).select_related('match', 'team_a', 'team_b', 'winner_team')
        for node in nodes:
            m = node.match
            if not m or m.status not in ('finished', 'forfeit'):
                continue
            wa, wb = m.team_a_id, m.team_b_id
            if wa not in table or wb not in table:
                continue
            sa, sb = m.team_a_score, m.team_b_score
            table[wa]['scored'] += sa
            table[wa]['against'] += sb
            table[wb]['scored'] += sb
            table[wb]['against'] += sa
            if m.winner_team_id == wa:
                table[wa]['wins'] += 1
                table[wa]['points'] += 3
                table[wb]['losses'] += 1
            elif m.winner_team_id == wb:
                table[wb]['wins'] += 1
                table[wb]['points'] += 3
                table[wa]['losses'] += 1
        sorted_table = sorted(
            table.values(),
            key=lambda r: (-r['points'], -(r['scored'] - r['against']), -r['scored']),
        )
        all_standings.append(sorted_table)
    return all_standings


@transaction.atomic
def maybe_build_knockout_from_groups(competition_id: int) -> Optional[dict]:
    try:
        comp = GeniusCompetition.objects.get(pk=competition_id)
    except GeniusCompetition.DoesNotExist:
        return None
    cfg = comp.get_config().get('group_stage') or {}
    if not cfg or cfg.get('knockout_built'):
        return None
    unfinished = GeniusBracketNode.objects.filter(
        competition=comp,
        round_order=0,
        phase_key='groups',
        match__isnull=False,
    ).exclude(match__status__in=('finished', 'forfeit')).exists()
    if unfinished:
        return None
    standings = _compute_group_standings(comp)
    advancers: List[GeniusTeam] = []
    for group_table in standings:
        for row in group_table[:2]:
            advancers.append(GeniusTeam.objects.get(pk=row['team_id']))
    n = len(advancers)
    if n < 2:
        return None
    bracket_size = _next_power_of_2(n)
    slots: List[Optional[GeniusTeam]] = list(advancers)
    while len(slots) < bracket_size:
        slots.append(None)
    num_rounds = int(math.log2(bracket_size))
    final_round = num_rounds
    nodes_by_round: List[List[GeniusBracketNode]] = []
    round0: List[GeniusBracketNode] = []
    for pos in range(bracket_size // 2):
        ta, tb = slots[pos * 2], slots[pos * 2 + 1]
        phase_key, phase_label = _round_phase(comp, 1, final_round)
        node = GeniusBracketNode.objects.create(
            competition=comp,
            phase_key=phase_key,
            phase_label=phase_label,
            round_order=1,
            position=pos,
            team_a=ta,
            team_b=tb,
        )
        if ta and not tb:
            node.winner_team = ta
            node.save(update_fields=['winner_team'])
        elif tb and not ta:
            node.team_a = tb
            node.team_b = None
            node.winner_team = tb
            node.save(update_fields=['winner_team', 'team_a', 'team_b'])
        elif ta and tb:
            node.match = create_match(comp.id, ta.id, tb.id)
            node.save(update_fields=['match'])
        round0.append(node)
    nodes_by_round.append(round0)
    for r in range(1, num_rounds):
        prev = nodes_by_round[r - 1]
        round_nodes: List[GeniusBracketNode] = []
        for pos in range(len(prev) // 2):
            phase_key, phase_label = _round_phase(comp, r + 1, final_round)
            parent = GeniusBracketNode.objects.create(
                competition=comp,
                phase_key=phase_key,
                phase_label=phase_label,
                round_order=r + 1,
                position=pos,
            )
            prev[pos * 2].parent = parent
            prev[pos * 2].save(update_fields=['parent'])
            prev[pos * 2 + 1].parent = parent
            prev[pos * 2 + 1].save(update_fields=['parent'])
            round_nodes.append(parent)
            _sync_parent_from_children(parent)
            for child in parent.children.all():
                if child.winner_team_id and child.parent_id == parent.id:
                    _propagate_bracket_winner(child)
        nodes_by_round.append(round_nodes)
    cfg['knockout_built'] = True
    comp.config['group_stage'] = cfg
    comp.save(update_fields=['config'])
    match_count = GeniusBracketNode.objects.filter(
        competition=comp, round_order__gte=1, match__isnull=False,
    ).count()
    notify_competition_started(comp)
    return {'ok': True, 'advancers': n, 'knockout_matches': match_count}


def _sync_parent_from_children(parent: GeniusBracketNode) -> None:
    children = list(parent.children.order_by('position'))
    if len(children) < 2:
        return
    c0, c1 = children[0], children[1]
    if c0.winner_team_id:
        parent.team_a = c0.winner_team
    if c1.winner_team_id:
        parent.team_b = c1.winner_team
    parent.save(update_fields=['team_a', 'team_b'])
    parent.refresh_from_db()
    if parent.team_a_id and parent.team_b_id and not parent.match_id:
        parent.match = create_match(parent.competition_id, parent.team_a_id, parent.team_b_id)
        parent.save(update_fields=['match'])
    elif parent.team_a_id and parent.team_b_id is None and c1.winner_team_id:
        parent.team_b = c1.winner_team
        parent.save(update_fields=['team_b'])
        parent.refresh_from_db()
        if parent.team_a_id and parent.team_b_id and not parent.match_id:
            parent.match = create_match(parent.competition_id, parent.team_a_id, parent.team_b_id)
            parent.save(update_fields=['match'])


def _propagate_bracket_winner(node: GeniusBracketNode) -> None:
    parent = node.parent
    if not parent:
        comp = node.competition
        if comp and node.winner_team_id:
            final_round = comp.bracket_nodes.order_by('-round_order').values_list(
                'round_order', flat=True,
            ).first()
            if final_round is not None and node.round_order == final_round:
                comp.status = 'completed'
                comp.save(update_fields=['status'])
                try:
                    from core.xp import award_genius_competition_xp
                    award_genius_competition_xp(comp)
                except Exception:
                    pass
        return
    if node.position % 2 == 0:
        parent.team_a = node.winner_team
        parent.save(update_fields=['team_a'])
    else:
        parent.team_b = node.winner_team
        parent.save(update_fields=['team_b'])
    parent.refresh_from_db()
    if parent.team_a_id and parent.team_b_id and not parent.match_id:
        parent.match = create_match(parent.competition_id, parent.team_a_id, parent.team_b_id)
        parent.save(update_fields=['match'])


@transaction.atomic
def build_competition_bracket(competition_id: int) -> dict:
    try:
        comp = GeniusCompetition.objects.get(pk=competition_id)
    except GeniusCompetition.DoesNotExist:
        raise GeniusError('Concours introuvable.', 'not_found')
    if comp.bracket_nodes.exists():
        raise GeniusError('Le bracket existe déjà.', 'already_built')
    teams = [
        r.team for r in GeniusRegistration.objects.filter(
            competition=comp, status='roster_locked',
        ).select_related('team').order_by('registered_at')
    ]
    n = len(teams)
    if n < 2:
        raise GeniusError('Au moins 2 équipes avec roster verrouillé.', 'insufficient_teams')

    if n >= 8:
        match_count = _build_group_stage(comp, teams)
        comp.status = 'in_progress'
        comp.save(update_fields=['status', 'config'])
        notify_competition_started(comp)
        return {'ok': True, 'teams': n, 'rounds': 1, 'matches': match_count, 'format': 'group_stage'}

    bracket_size = _next_power_of_2(n)
    slots: List[Optional[GeniusTeam]] = list(teams)
    while len(slots) < bracket_size:
        slots.append(None)

    num_rounds = int(math.log2(bracket_size))
    final_round = num_rounds - 1
    nodes_by_round: List[List[GeniusBracketNode]] = []

    round0: List[GeniusBracketNode] = []
    for pos in range(bracket_size // 2):
        ta, tb = slots[pos * 2], slots[pos * 2 + 1]
        phase_key, phase_label = _round_phase(comp, 0, final_round)
        node = GeniusBracketNode.objects.create(
            competition=comp,
            phase_key=phase_key,
            phase_label=phase_label,
            round_order=0,
            position=pos,
            team_a=ta,
            team_b=tb,
        )
        if ta and not tb:
            node.winner_team = ta
            node.save(update_fields=['winner_team'])
        elif tb and not ta:
            node.team_a = tb
            node.team_b = None
            node.winner_team = tb
            node.save(update_fields=['winner_team', 'team_a', 'team_b'])
        elif ta and tb:
            node.match = create_match(comp.id, ta.id, tb.id)
            node.save(update_fields=['match'])
        round0.append(node)
    nodes_by_round.append(round0)

    for r in range(1, num_rounds):
        prev = nodes_by_round[r - 1]
        round_nodes: List[GeniusBracketNode] = []
        for pos in range(len(prev) // 2):
            phase_key, phase_label = _round_phase(comp, r, final_round)
            parent = GeniusBracketNode.objects.create(
                competition=comp,
                phase_key=phase_key,
                phase_label=phase_label,
                round_order=r,
                position=pos,
            )
            prev[pos * 2].parent = parent
            prev[pos * 2].save(update_fields=['parent'])
            prev[pos * 2 + 1].parent = parent
            prev[pos * 2 + 1].save(update_fields=['parent'])
            round_nodes.append(parent)
            _sync_parent_from_children(parent)
            for child in parent.children.all():
                if child.winner_team_id and child.parent_id == parent.id:
                    _propagate_bracket_winner(child)
        nodes_by_round.append(round_nodes)

    comp.status = 'in_progress'
    comp.save(update_fields=['status'])
    match_count = GeniusMatch.objects.filter(competition=comp).count()
    notify_competition_started(comp)
    return {'ok': True, 'teams': n, 'rounds': num_rounds, 'matches': match_count}


@transaction.atomic
def start_competition(competition_id: int, user) -> dict:
    try:
        comp = GeniusCompetition.objects.get(pk=competition_id)
    except GeniusCompetition.DoesNotExist:
        raise GeniusError('Concours introuvable.', 'not_found')
    if comp.status not in ('registration', 'roster_locked'):
        raise GeniusError('Le concours ne peut pas être lancé dans cet état.', 'invalid_state')
    locked = GeniusRegistration.objects.filter(competition=comp, status='roster_locked').count()
    if locked < 2:
        raise GeniusError('Au moins 2 rosters verrouillés requis.', 'insufficient_teams')
    comp.status = 'roster_locked'
    comp.save(update_fields=['status'])
    return build_competition_bracket(competition_id)
