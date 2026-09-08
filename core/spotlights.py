"""Vitrine accueil — lauréat annuel, équipe championne, élève de la semaine."""
from __future__ import annotations

from datetime import timedelta
from django.core.cache import cache
from django.db.models import Avg, Count, F, FloatField, Sum
from django.db.models.expressions import ExpressionWrapper
from django.db.models.functions import TruncDate
from django.utils import timezone

CACHE_KEY = 'home_spotlights_v4'
CACHE_TTL = 8 * 60

_MONTHS_FR = (
    'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
    'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
)


def academic_year_label(d=None) -> str:
    d = d or timezone.localdate()
    if d.month >= 8:
        return f'{d.year}-{d.year + 1}'
    return f'{d.year - 1}-{d.year}'


def week_label(d=None) -> str:
    d = d or timezone.localdate()
    monday = d - timedelta(days=d.weekday())
    return f'Semaine du {monday.day} {_MONTHS_FR[monday.month - 1]}'


def bust_spotlight_cache():
    cache.delete(CACHE_KEY)


def get_home_spotlights():
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    try:
        data = _build_spotlights()
    except Exception:
        data = _empty()
    cache.set(CACHE_KEY, data, CACHE_TTL)
    return data


def _empty():
    year = academic_year_label()
    return {
        'laureate': None,
        'laureates': [],
        'team_week': None,
        'student_week': None,
        'has_any': False,
        'hall_year': year,
        'hall_week_label': week_label(),
    }


def _build_spotlights():
    year = academic_year_label()
    laureate = team = student = None
    try:
        laureate = _pinned_laureate(year) or _auto_laureate(year)
    except Exception:
        laureate = None
    try:
        team = _auto_champion_team()
    except Exception:
        team = None
    try:
        student = _auto_student_of_week()
    except Exception:
        student = None
    return {
        'laureate': laureate,
        'laureates': [laureate] if laureate else [],
        'team_week': team,
        'student_week': student,
        'has_any': bool(laureate or team or student),
        'hall_year': year,
        'hall_week_label': week_label(),
    }


def _display_name(user, profile=None):
    profile = profile or getattr(user, 'profile', None)
    if profile:
        full = f'{(profile.first_name or "").strip()} {(profile.last_name or "").strip()}'.strip()
        if full:
            return full
        if profile.first_name:
            return profile.first_name
    return (user.first_name or user.username or 'Élève').strip()


def _avatar_url(profile):
    if not profile:
        return None
    photo = getattr(profile, 'avatar', None)
    if not photo:
        return None
    try:
        return photo.url
    except Exception:
        return None


def _initials(name: str) -> str:
    parts = [p for p in (name or '').split() if p]
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _card(
    *,
    name,
    meta='',
    school='',
    serie='',
    score='',
    body='',
    photo_url=None,
    emblem='',
    week='',
    year='',
    initials='',
    url='',
    team_id=None,
    members=None,
    matches=None,
    last_match='',
):
    return {
        'name': name,
        'title': name,
        'meta': meta,
        'school': school,
        'serie': serie,
        'score': score,
        'body': body,
        'photo_url': photo_url,
        'emblem': emblem,
        'week_label': week,
        'academic_year': year,
        'initials': initials or _initials(name),
        'url': url,
        'team_id': team_id,
        'members': members or [],
        'matches': matches or [],
        'last_match': last_match,
    }


def _pinned_laureate(year: str):
    from core.models import SiteSpotlight
    s = (
        SiteSpotlight.objects.filter(kind=SiteSpotlight.KIND_LAUREATE, is_published=True)
        .order_by('pin_order', '-created_at')
        .first()
    )
    if not s:
        return None
    photo = None
    if s.photo:
        try:
            photo = s.photo.url
        except Exception:
            photo = None
    meta_bits = [s.school, s.serie, s.subtitle]
    return _card(
        name=s.title,
        meta=' · '.join(p for p in meta_bits if p),
        school=s.school,
        serie=s.serie,
        score='',
        body=s.body or 'Lauréat du site — meilleure note parmi les élèves OU TOU BON.',
        photo_url=photo,
        year=s.academic_year or year,
    )


def _auto_laureate(year: str):
    """Élève du site avec la meilleure note BAC estimée (pas le BAC national)."""
    from django.contrib.auth.models import User
    from accounts.models import DiagnosticResult, UserProfile
    from core.models import QuizSession
    from core.series_data import SERIES
    from core.subject_scores import estimate_bac_score

    quiz_rows = (
        QuizSession.objects.filter(
            user__is_staff=False,
            user__is_superuser=False,
            user__agent__isnull=True,
            total__gt=0,
        )
        .values('user_id', 'subject')
        .annotate(
            n=Count('id'),
            avg_pct=Avg(
                ExpressionWrapper(100.0 * F('score') / F('total'), output_field=FloatField())
            ),
        )
    )
    by_user = {}
    quiz_counts = {}
    for row in quiz_rows:
        uid = row['user_id']
        quiz_counts[uid] = quiz_counts.get(uid, 0) + int(row['n'] or 0)
        if row['avg_pct'] is None:
            continue
        by_user.setdefault(uid, {})[row['subject']] = int(round(row['avg_pct']))

    diag_rows = DiagnosticResult.objects.filter(
        user_id__in=list(by_user.keys()) or [0]
    ).values_list('user_id', 'subject', 'score')
    for uid, subject, score in diag_rows:
        scores = by_user.setdefault(uid, {})
        if subject not in scores:
            scores[subject] = int(score or 0)

    eligible = [uid for uid, n in quiz_counts.items() if n >= 3 and len(by_user.get(uid, {})) >= 2]
    if not eligible:
        return None

    profiles = {
        p.user_id: p
        for p in UserProfile.objects.filter(user_id__in=eligible).select_related('user')
    }
    users = {u.id: u for u in User.objects.filter(id__in=eligible)}

    best = None
    best_score = -1
    for uid in eligible:
        user = users.get(uid)
        if not user:
            continue
        profile = profiles.get(uid)
        serie = (profile.serie if profile and profile.serie else 'SVT')
        bac = estimate_bac_score(by_user[uid], serie, SERIES)
        if bac > best_score:
            best_score = bac
            best = (user, profile, bac, serie)

    if not best or best_score <= 0:
        return None
    user, profile, bac, serie = best
    name = _display_name(user, profile)
    school = (profile.school if profile else '') or ''
    return _card(
        name=name,
        meta=' · '.join(p for p in (school, serie) if p),
        school=school,
        serie=serie,
        score='',
        body='Meilleure note estimée parmi les élèves OU TOU BON qui préparent le BAC.',
        photo_url=_avatar_url(profile),
        year=year,
    )


def _auto_champion_team():
    try:
        from core.genius.models import GeniusMembership, GeniusTeam, GeniusTeamStats
    except Exception:
        return None

    stats = (
        GeniusTeamStats.objects.filter(team__is_active=True)
        .select_related('team')
        .order_by('-ranking_points', '-wins', '-total_score', '-team_xp')
        .first()
    )
    team = stats.team if stats else (
        GeniusTeam.objects.filter(is_active=True).order_by('-created_at').first()
    )
    if not team:
        return None
    members_qs = list(
        GeniusMembership.objects.filter(team=team, status='active').select_related('user', 'user__profile')[:4]
    )
    member_cards = []
    for m in members_qs:
        u = m.user
        prof = getattr(u, 'profile', None)
        nm = _display_name(u, prof)
        member_cards.append({
            'name': nm,
            'initials': _initials(nm),
            'photo_url': _avatar_url(prof),
            'is_captain': m.role == 'captain',
        })
    history = []
    last_match = ''
    try:
        from core.genius.services import team_match_history
        history = team_match_history(team, 3)
        if history:
            h0 = history[0]
            last_match = (
                f"{'Victoire' if h0['won'] else 'Défaite'} {h0['score_us']}-{h0['score_them']} vs {h0['opponent']}"
            )
    except Exception:
        history = []
    from django.urls import reverse
    try:
        url = reverse('genius_club', args=[team.id])
    except Exception:
        url = f'/dashboard/genius/club/{team.id}/'
    n_members = len(member_cards) or members
    if stats and (stats.matches_played or stats.ranking_points or stats.team_xp):
        bits = []
        if stats.ranking_points:
            bits.append(f'{stats.ranking_points} pts')
        if stats.wins:
            bits.append(f'{stats.wins}V')
        if stats.matches_played:
            bits.append(f'{stats.matches_played} matchs')
        meta = ' · '.join(bits) if bits else f'{n_members} membre{"s" if n_members != 1 else ""}'
        body = 'Clique pour voir les membres et l’historique du club.'
    else:
        meta = f'{n_members} membre{"s" if n_members != 1 else ""}'
        body = 'Clique pour ouvrir la fiche du club.'
    return _card(
        name=team.name,
        meta=meta,
        body=body,
        emblem=team.emblem_emoji or '🧠',
        url=url,
        team_id=team.id,
        members=member_cards,
        matches=history,
        last_match=last_match,
    )


def _auto_student_of_week():
    from django.contrib.auth.models import User
    from accounts.models import UserProfile
    from core.models import ChatMessage, LearningEvent, QuizSession, UserStats, XpEvent

    since = timezone.now() - timedelta(days=7)
    xp_map = dict(
        XpEvent.objects.filter(created_at__gte=since, amount__gt=0)
        .values('user_id')
        .annotate(s=Sum('amount'))
        .values_list('user_id', 's')
    )
    quiz_map = {
        r['user_id']: r
        for r in QuizSession.objects.filter(
            completed_at__gte=since,
            user__is_staff=False,
            user__is_superuser=False,
            total__gt=0,
        )
        .values('user_id')
        .annotate(
            n=Count('id'),
            avg=Avg(ExpressionWrapper(100.0 * F('score') / F('total'), output_field=FloatField())),
        )
    }
    exo_map = {
        r['user_id']: r
        for r in LearningEvent.objects.filter(
            created_at__gte=since,
            event_type='exercise_corrected',
            user__is_staff=False,
        )
        .values('user_id')
        .annotate(n=Count('id'), avg=Avg('score_pct'))
    }
    days_map = dict(
        LearningEvent.objects.filter(created_at__gte=since, user__is_staff=False)
        .annotate(day=TruncDate('created_at'))
        .values('user_id')
        .annotate(d=Count('day', distinct=True))
        .values_list('user_id', 'd')
    )
    course_map = dict(
        LearningEvent.objects.filter(
            created_at__gte=since, event_type='course_chapter', user__is_staff=False
        )
        .values('user_id')
        .annotate(n=Count('id'))
        .values_list('user_id', 'n')
    )
    chat_map = dict(
        ChatMessage.objects.filter(created_at__gte=since, role='user', user__is_staff=False)
        .values('user_id')
        .annotate(n=Count('id'))
        .values_list('user_id', 'n')
    )

    user_ids = set(xp_map) | set(quiz_map) | set(exo_map) | set(days_map)
    if not user_ids:
        return None

    stats_map = {
        s.user_id: s
        for s in UserStats.objects.filter(user_id__in=user_ids)
    }
    profiles = {
        p.user_id: p
        for p in UserProfile.objects.filter(user_id__in=user_ids)
    }
    users = {u.id: u for u in User.objects.filter(id__in=user_ids, is_staff=False, is_superuser=False)}

    best = None
    best_pts = -1
    for uid, user in users.items():
        quiz = quiz_map.get(uid) or {}
        exo = exo_map.get(uid) or {}
        quiz_n = int(quiz.get('n') or 0)
        exo_n = int(exo.get('n') or 0)
        days = int(days_map.get(uid) or 0)
        week_xp = int(xp_map.get(uid) or 0)
        if quiz_n + exo_n < 1 and days < 1 and week_xp <= 0:
            continue
        quiz_avg = float(quiz.get('avg') or 0)
        exo_avg = float(exo.get('avg') or 0)
        streak = int(getattr(profiles.get(uid), 'streak', 0) or 0)
        chat_n = min(int(chat_map.get(uid) or 0), 20)
        course_n = int(course_map.get(uid) or 0)
        week_xp = int(xp_map.get(uid) or 0)
        stats = stats_map.get(uid)
        lifetime_quiz = int(getattr(stats, 'quiz_completes', 0) or 0)
        pts = (
            week_xp * 1.15
            + quiz_n * 14
            + quiz_avg * 2.2
            + exo_n * 22
            + exo_avg * 1.4
            + days * 45
            + min(streak, 30) * 14
            + course_n * 16
            + chat_n * 2
            + min(lifetime_quiz, 80) * 0.4
        )
        if pts > best_pts:
            best_pts = pts
            best = (user, uid, quiz_n, quiz_avg, exo_n, days, streak, week_xp)

    if not best:
        cutoff = timezone.localdate() - timedelta(days=21)
        cand = (
            UserProfile.objects.filter(
                user__is_staff=False,
                user__is_superuser=False,
                last_activity__gte=cutoff,
            )
            .select_related('user')
            .order_by('-streak', '-last_activity')
            .first()
        )
        if not cand:
            return None
        user = cand.user
        uid = user.id
        profiles[uid] = cand
        quiz_n = exo_n = days = week_xp = 0
        quiz_avg = 0
        streak = int(cand.streak or 0)
        best = (user, uid, quiz_n, quiz_avg, exo_n, days, streak, week_xp)
    user, uid, quiz_n, quiz_avg, exo_n, days, streak, week_xp = best
    profile = profiles.get(uid)
    name = _display_name(user, profile)
    school = (profile.school if profile else '') or ''
    bits = []
    if quiz_n:
        bits.append(f'{quiz_n} quiz')
    if exo_n:
        bits.append(f'{exo_n} exo')
    if streak:
        bits.append(f'{streak} j. de série')
    if week_xp:
        bits.append(f'+{week_xp} XP')
    return _card(
        name=name,
        meta=' · '.join(bits) or school,
        school=school,
        serie=(profile.serie if profile else '') or '',
        body='Choisi automatiquement : notes, régularité, exercices, série et XP de la semaine.',
        photo_url=_avatar_url(profile),
        week=week_label(),
    )
