import json

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import Friendship, UserProfile

from .models import GeniusCompetition, GeniusInvitation, GeniusJoinRequest, GeniusMatch, GeniusRegistration, GeniusTeam
from .notifications import (
    leaderboard_payload,
    mark_notifications_read,
    notifications_payload,
    prizes_payload,
    team_stats_payload,
    unread_notification_count,
    upcoming_matches_for_user,
    send_scheduled_match_reminders,
)
from .constants import competition_status_label
from .services import (
    GeniusError,
    bracket_payload,
    create_team,
    create_friendly_match,
    create_friendly_match_by_code,
    get_user_active_team,
    invite_user,
    join_team_with_code,
    list_challengeable_teams,
    list_discoverable_teams,
    lock_roster,
    mark_presence,
    match_state_payload,
    register_team_for_competition,
    request_join_team,
    request_remove_member,
    respond_invitation,
    respond_join_request,
    set_team_open_for_requests,
    start_competition,
    start_match,
    submit_vote,
    captain_lock_answer,
    team_members_payload,
    transfer_captain,
    schedule_match,
    team_match_history,
    team_activity_feed,
)


def _parse_json(request):
    try:
        return json.loads(request.body or '{}'), None
    except json.JSONDecodeError:
        return None, JsonResponse({'error': 'JSON invalide'}, status=400)


def _genius_error_response(exc: GeniusError):
    return JsonResponse({'error': exc.message, 'code': exc.code}, status=400)


def _ok_hub(user, **payload):
    _invalidate_hub_cache(user.pk)
    return JsonResponse({'ok': True, **payload})


def _team_payload(team):
    members = team_members_payload(team)
    return {
        'id': team.id,
        'name': team.name,
        'description': team.description,
        'emblem': team.emblem_emoji,
        'invite_code': team.invite_code,
        'captain_id': team.captain_id,
        'member_count': team.member_count(),
        'max_size': 4,
        'members': members,
        'transfer_candidates': [
            m for m in members if not m['is_captain'] and m['captain_eligible']
        ],
        'is_full': team.is_full(),
        'is_open_for_requests': team.is_open_for_requests,
        'stats': team_stats_payload(team),
    }


def _hub_payload(user):
    from django.core.cache import cache

    cache_key = f'genius:hub:{user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    team = get_user_active_team(user)
    team_data = _team_payload(team) if team else None
    my_role = None
    if team:
        m = team.memberships.filter(user=user, status='active').first()
        my_role = m.role if m else None

    from accounts.names import alias_map_for, overlay_alias
    aliases = alias_map_for(user)
    if team_data:
        for m in team_data.get('members') or []:
            m['display_name'] = overlay_alias(aliases, m.get('user_id'), m.get('display_name'))

    pending_invites = []
    for inv in GeniusInvitation.objects.filter(to_user=user, status='pending').select_related('team', 'from_user')[:12]:
        if inv.is_expired():
            continue
        pending_invites.append({
            'id': inv.id,
            'team_name': inv.team.name,
            'from_user_id': inv.from_user_id,
            'from_name': overlay_alias(aliases, inv.from_user_id, inv.from_user.first_name or inv.from_user.username),
            'expires_at': inv.expires_at.isoformat() if inv.expires_at else None,
        })

    friends = []
    accepted = Friendship.objects.filter(
        Q(from_user=user, status='accepted') | Q(to_user=user, status='accepted'),
    ).select_related('from_user', 'to_user')[:40]
    for f in accepted:
        other = f.to_user if f.from_user_id == user.id else f.from_user
        if other.is_staff:
            continue
        friends.append({
            'id': other.id,
            'name': overlay_alias(aliases, other.id, other.first_name or other.username),
            'username': other.username,
        })

    competitions = []
    try:
        from core.genius.schedule import ensure_weekly_competition
        # Ne pas bloquer le hub à chaque vue : sync au plus 1× / 5 min
        if not cache.get('genius:weekly_synced'):
            ensure_weekly_competition()
            cache.set('genius:weekly_synced', 1, 300)
    except Exception:
        pass
    comps = list(
        GeniusCompetition.objects.filter(
            status__in=['draft', 'registration', 'roster_locked', 'in_progress'],
        ).order_by('-start_date', '-created_at')[:8]
    )
    reg_by_comp = {}
    if team and comps:
        for reg in GeniusRegistration.objects.filter(
            competition_id__in=[c.pk for c in comps], team=team,
        ):
            reg_by_comp[reg.competition_id] = reg
    from django.db.models import Count
    counts = {
        row['competition_id']: row['c']
        for row in GeniusRegistration.objects.filter(
            competition_id__in=[c.pk for c in comps],
            status__in=['registered', 'roster_locked'],
        ).values('competition_id').annotate(c=Count('id'))
    } if comps else {}
    for comp in comps:
        reg = reg_by_comp.get(comp.id)
        competitions.append({
            'id': comp.id,
            'name': comp.name,
            'description': (comp.description or '')[:200],
            'status': comp.status,
            'status_label': competition_status_label(comp.status),
            'start_date': comp.start_date.isoformat() if comp.start_date else None,
            'registered_count': counts.get(comp.id, 0),
            'registration': {
                'status': reg.status if reg else None,
                'roster_locked': reg.roster_is_locked() if reg else False,
            } if team else None,
        })

    active_matches = []
    if team:
        for m in GeniusMatch.objects.filter(
            Q(team_a=team) | Q(team_b=team),
            status__in=['scheduled', 'lobby', 'in_progress'],
        ).select_related('team_a', 'team_b')[:5]:
            active_matches.append({
                'id': m.id,
                'team_a': m.team_a.name,
                'team_b': m.team_b.name,
                'status': m.status,
            })

    pending_join_requests = []
    if team and my_role == 'captain':
        for req in GeniusJoinRequest.objects.filter(team=team, status='pending').select_related('from_user')[:20]:
            pending_join_requests.append({
                'id': req.id,
                'from_name': overlay_alias(aliases, req.from_user_id, req.from_user.first_name or req.from_user.username),
                'from_user_id': req.from_user_id,
                'message': req.message,
                'created_at': req.created_at.isoformat(),
            })

    discoverable = list_discoverable_teams(user) if not team else []

    payload = {
        'team': team_data,
        'my_role': my_role,
        'pending_invitations': pending_invites,
        'pending_join_requests': pending_join_requests,
        'discoverable_teams': discoverable,
        'friends': friends,
        'competitions': competitions,
        'active_matches': active_matches,
        'challengeable_teams': list_challengeable_teams(team.id if team else None),
        'notifications': notifications_payload(user, limit=8),
        'unread_notifications': unread_notification_count(user),
        'leaderboard': leaderboard_payload(limit=10),
        'upcoming_matches': upcoming_matches_for_user(user),
    }
    cache.set(cache_key, payload, 45)
    return payload


def _invalidate_hub_cache(user_id: int):
    try:
        from django.core.cache import cache
        cache.delete(f'genius:hub:{user_id}')
    except Exception:
        pass


@login_required
def genius_hub_view(request):
    import json
    from django.core.serializers.json import DjangoJSONEncoder

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    hub = _hub_payload(request.user)
    return render(request, 'core/genius/hub.html', {
        'active_page': 'genius',
        'profile': profile,
        'has_genius_team': hub.get('team') is not None,
        'hub_initial': hub,
        'hub_initial_json': json.dumps(hub, cls=DjangoJSONEncoder),
        'is_guest': False,
    })


def genius_hub_entry(request):
    """Entrée unique : guest voit concours/classement réels ; compte → hub complet."""
    if not request.user.is_authenticated:
        if not request.session.get('guest_mode'):
            return redirect('/login/?next=' + request.get_full_path())
        return _genius_hub_guest(request)
    return genius_hub_view(request)


def _genius_hub_guest(request):
    import json
    from django.core.serializers.json import DjangoJSONEncoder
    from django.core.cache import cache
    from django.db.models import Count

    try:
        from core.genius.schedule import ensure_weekly_competition
        if not cache.get('genius:weekly_synced'):
            ensure_weekly_competition()
            cache.set('genius:weekly_synced', 1, 300)
    except Exception:
        pass

    comps = list(
        GeniusCompetition.objects.filter(
            status__in=['draft', 'registration', 'roster_locked', 'in_progress'],
        ).order_by('-start_date', '-created_at')[:8]
    )
    counts = {
        row['competition_id']: row['c']
        for row in GeniusRegistration.objects.filter(
            competition_id__in=[c.pk for c in comps],
            status__in=['registered', 'roster_locked'],
        ).values('competition_id').annotate(c=Count('id'))
    } if comps else {}
    competitions = [{
        'id': c.id,
        'name': c.name,
        'description': (c.description or '')[:200],
        'status': c.status,
        'status_label': competition_status_label(c.status),
        'start_date': c.start_date.isoformat() if c.start_date else None,
        'registered_count': counts.get(c.id, 0),
        'registration': None,
    } for c in comps]

    hub = {
        'team': None,
        'my_role': None,
        'pending_invitations': [],
        'pending_join_requests': [],
        'discoverable_teams': list_discoverable_teams(request.user) if False else [],
        'friends': [],
        'competitions': competitions,
        'active_matches': [],
        'challengeable_teams': [],
        'notifications': [],
        'unread_notifications': 0,
        'leaderboard': leaderboard_payload(limit=10),
        'upcoming_matches': [],
        'is_guest': True,
    }
    # Équipes ouvertes (lecture seule)
    try:
        from django.contrib.auth.models import AnonymousUser
        # list_discoverable needs authenticated user for pending checks — use empty pending
        from django.db.models import Count, Q
        qs = (
            GeniusTeam.objects.filter(is_active=True, is_open_for_requests=True)
            .select_related('captain')
            .annotate(active_members=Count('memberships', filter=Q(memberships__status='active')))
            .order_by('-updated_at')[:24]
        )
        from core.genius.constants import MAX_TEAM_SIZE
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
                'pending_request': False,
            })
        hub['discoverable_teams'] = rows
    except Exception:
        hub['discoverable_teams'] = []

    return render(request, 'core/genius/hub.html', {
        'active_page': 'genius',
        'profile': None,
        'has_genius_team': False,
        'hub_initial': hub,
        'hub_initial_json': json.dumps(hub, cls=DjangoJSONEncoder),
        'is_guest': True,
    })


@login_required
def genius_team_view(request):
    team = get_user_active_team(request.user)
    if not team:
        return redirect('genius_hub')
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    membership = team.memberships.filter(user=request.user, status='active').first()
    return render(request, 'core/genius/team.html', {
        'active_page': 'genius',
        'profile': profile,
        'team': team,
        'my_role': membership.role if membership else None,
        'stats': team_stats_payload(team),
        'members': team_members_payload(team),
        'match_history': team_match_history(team, 20),
        'activity': team_activity_feed(team, 15),
        'is_own_team': True,
    })


def genius_club_view(request, team_id):
    """Fiche publique d'un club : membres, stats, historique."""
    if not request.user.is_authenticated and not request.session.get('guest_mode'):
        return redirect('/login/?next=' + request.get_full_path())
    team = get_object_or_404(GeniusTeam, pk=team_id, is_active=True)
    profile = None
    is_own = False
    if request.user.is_authenticated:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        my_team = get_user_active_team(request.user)
        is_own = bool(my_team and my_team.id == team.id)
    return render(request, 'core/genius/club.html', {
        'active_page': 'genius',
        'profile': profile,
        'team': team,
        'stats': team_stats_payload(team),
        'members': team_members_payload(team),
        'match_history': team_match_history(team, 20),
        'activity': team_activity_feed(team, 15),
        'is_own_team': is_own,
        'is_guest': not request.user.is_authenticated,
        'page_info_key': f'genius-club-{team.id}',
    })


@login_required
def genius_match_view(request, match_id):
    match = get_object_or_404(GeniusMatch.objects.select_related('team_a', 'team_b'), pk=match_id)
    is_player = match.players.filter(user=request.user).exists()
    if not is_player:
        return redirect('genius_hub')
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    membership = match.players.filter(user=request.user).select_related('team').first()
    return render(request, 'core/genius/match.html', {
        'active_page': 'genius',
        'profile': profile,
        'match_id': match.id,
        'my_team_id': membership.team_id,
        'is_captain': membership.team.captain_id == request.user.id,
    })


@login_required
def genius_competition_view(request, competition_id):
    from django.core.cache import cache

    comp = get_object_or_404(GeniusCompetition, pk=competition_id)
    team = get_user_active_team(request.user)
    reg = None
    if team:
        reg = GeniusRegistration.objects.filter(competition=comp, team=team).only(
            'status', 'roster_locked_at',
        ).first()

    stamp = int(comp.updated_at.timestamp()) if comp.updated_at else 0
    cache_key = f'genius:comp_static:{comp.id}:{stamp}'
    static = cache.get(cache_key)
    if not isinstance(static, dict):
        bracket = bracket_payload(comp)
        bracket_by_round = {}
        for node in bracket:
            bracket_by_round.setdefault(node['round_order'], []).append(node)
        static = {
            'bracket': bracket,
            'bracket_by_round': sorted(bracket_by_round.items()),
            'prizes': prizes_payload(comp),
            'leaderboard': leaderboard_payload(comp.id, limit=8),
            'locked_teams_count': comp.registrations.filter(status='roster_locked').count(),
        }
        cache.set(cache_key, static, 45)

    return render(request, 'core/genius/competition.html', {
        'active_page': 'genius',
        'competition': comp,
        'status_label': competition_status_label(comp.status),
        'registration': reg,
        'bracket': static['bracket'],
        'bracket_by_round': static['bracket_by_round'],
        'prizes': static['prizes'],
        'leaderboard': static['leaderboard'],
        'team': team,
        'locked_teams_count': static['locked_teams_count'],
        'can_start_competition': request.user.is_staff and comp.status in ('registration', 'roster_locked'),
        'page_info_key': f'genius-comp-{comp.id}',
    })


@login_required
@require_GET
def api_genius_hub(request):
    from django.core.cache import cache
    # Rappels match : au plus 1× / 2 min (pas à chaque poll hub)
    if not cache.get('genius:reminders_tick'):
        try:
            send_scheduled_match_reminders()
        except Exception:
            pass
        cache.set('genius:reminders_tick', 1, 120)
    return JsonResponse(_hub_payload(request.user))


@login_required
@require_POST
def api_genius_team_create(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        team = create_team(
            request.user,
            data.get('name', ''),
            data.get('description', ''),
            data.get('emblem', '🧠'),
            bool(data.get('is_open_for_requests', True)),
        )
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'team': _team_payload(team)})
    except GeniusError as e:
        return _genius_error_response(e)


@login_required
@require_POST
def api_genius_team_join(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        team = join_team_with_code(request.user, data.get('code', ''))
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'team': _team_payload(team)})
    except GeniusError as e:
        return _genius_error_response(e)


@login_required
@require_POST
def api_genius_join_request(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        req = request_join_team(
            request.user,
            int(data.get('team_id')),
            data.get('message', ''),
        )
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'request_id': req.id, 'status': req.status})
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_join_respond(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        result = respond_join_request(
            request.user,
            int(data.get('request_id')),
            bool(data.get('accept')),
        )
        return JsonResponse(result)
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_transfer_captain(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        result = transfer_captain(
            request.user,
            int(data.get('team_id')),
            int(data.get('user_id')),
        )
        return JsonResponse(result)
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_team_open(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        team = set_team_open_for_requests(
            request.user,
            int(data.get('team_id')),
            bool(data.get('open', True)),
        )
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'is_open_for_requests': team.is_open_for_requests})
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_team_invite(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        inv = invite_user(request.user, int(data.get('team_id')), int(data.get('user_id')))
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'invitation_id': inv.id})
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_invitation_respond(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        result = respond_invitation(
            request.user,
            int(data.get('invitation_id')),
            bool(data.get('accept')),
        )
        return JsonResponse(result)
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_team_remove(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        result = request_remove_member(
            request.user,
            int(data.get('team_id')),
            int(data.get('user_id')),
        )
        return JsonResponse(result)
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_competition_register(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        reg = register_team_for_competition(
            request.user,
            int(data.get('competition_id')),
            int(data.get('team_id')),
        )
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'status': reg.status})
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_competition_lock(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        reg = lock_roster(
            request.user,
            int(data.get('competition_id')),
            int(data.get('team_id')),
        )
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'status': reg.status})
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_match_presence(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        result = mark_presence(
            request.user,
            int(data.get('match_id')),
            bool(data.get('present', True)),
        )
        return JsonResponse(result)
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_match_start(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        match = start_match(request.user, int(data.get('match_id')))
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'status': match.status})
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_match_vote(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        result = submit_vote(request.user, int(data.get('match_id')), data.get('choice', ''))
        return JsonResponse(result)
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_match_lock(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        result = captain_lock_answer(request.user, int(data.get('match_id')), data.get('choice', ''))
        return JsonResponse(result)
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_match_schedule(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        match = schedule_match(request.user, int(data.get('match_id')), data.get('scheduled_at', ''))
        return JsonResponse({
            'ok': True,
            'scheduled_at': match.scheduled_at.isoformat() if match.scheduled_at else None,
        })
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_GET
def api_genius_match_state(request):
    match_id = request.GET.get('match_id')
    if not match_id:
        return JsonResponse({'error': 'match_id requis'}, status=400)
    try:
        match = GeniusMatch.objects.get(pk=int(match_id))
    except (GeniusMatch.DoesNotExist, ValueError):
        return JsonResponse({'error': 'Match introuvable'}, status=404)
    if not match.players.filter(user=request.user).exists():
        return JsonResponse({'error': 'Accès refusé'}, status=403)
    return JsonResponse(match_state_payload(match, for_user=request.user))


@login_required
@require_GET
def api_genius_teams(request):
    team = get_user_active_team(request.user)
    return JsonResponse({
        'teams': list_challengeable_teams(team.id if team else None),
    })


@login_required
@require_POST
def api_genius_match_create(request):
    data, err = _parse_json(request)
    if err:
        return err
    try:
        if data.get('invite_code'):
            match = create_friendly_match_by_code(request.user, data.get('invite_code', ''))
        else:
            match = create_friendly_match(request.user, int(data.get('opponent_team_id')))
        _invalidate_hub_cache(request.user.pk)
        return JsonResponse({'ok': True, 'match_id': match.id})
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_POST
def api_genius_competition_start(request):
    if not request.user.is_staff:
        return JsonResponse({'error': 'Action réservée aux admins.'}, status=403)
    data, err = _parse_json(request)
    if err:
        return err
    try:
        result = start_competition(int(data.get('competition_id')), request.user)
        return JsonResponse(result)
    except (GeniusError, ValueError, TypeError) as e:
        if isinstance(e, GeniusError):
            return _genius_error_response(e)
        return JsonResponse({'error': 'Données invalides'}, status=400)


@login_required
@require_GET
def api_genius_notifications(request):
    return JsonResponse({
        'notifications': notifications_payload(request.user),
        'unread': unread_notification_count(request.user),
    })


@login_required
@require_POST
def api_genius_notifications_read(request):
    data, err = _parse_json(request)
    if err:
        return err
    ids = data.get('ids')
    if ids and not isinstance(ids, list):
        ids = None
    count = mark_notifications_read(request.user, ids)
    _invalidate_hub_cache(request.user.pk)
    return JsonResponse({'ok': True, 'marked': count, 'unread': unread_notification_count(request.user)})


@login_required
@require_GET
def api_genius_leaderboard(request):
    comp_id = request.GET.get('competition_id')
    try:
        limit = min(50, int(request.GET.get('limit', 20) or 20))
    except ValueError:
        limit = 20
    cid = None
    if comp_id:
        try:
            cid = int(comp_id)
        except ValueError:
            pass
    return JsonResponse({'leaderboard': leaderboard_payload(cid, limit=limit)})
