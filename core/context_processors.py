"""
Context processor: injecte user_lang, profile, unread_msg_count dans tous les templates.
"""
from accounts.models import UserProfile
from accounts.names import alias_map_for
from core.models import UserStats
from core.xp import get_user_xp
from django.conf import settings
import json


def _calc_user_xp(stats):
    return int(getattr(stats, 'xp_total', 0) or 0)


def user_lang(request):
    """
    Injecte {{ user_lang }} ('fr' ou 'kr') et {{ profile }} dans tous les contextes de template.
    """
    spa_mode = getattr(request, 'spa_mode', False)
    lang = 'fr'
    profile = None
    unread = 0
    unread_dot = False
    genius_unread = 0
    friend_aliases = {}
    user_xp = 0
    user_streak = 0
    user_xp_tier = 1
    user_xp_tier_pct = 0
    if request.user.is_authenticated:
        try:
            profile = (
                UserProfile.objects.filter(user=request.user)
                .only(
                    'preferred_lang',
                    'streak',
                    'langue_etrangere',
                    'coach_name',
                    'first_name',
                )
                .first()
            )
            if profile is None:
                profile, _ = UserProfile.objects.get_or_create(user=request.user)
            lang = profile.preferred_lang or 'fr'
            user_streak = profile.streak or 0
            try:
                friend_aliases = alias_map_for(request.user)
            except Exception:
                friend_aliases = {}
            if not spa_mode:
                from accounts.chat_groups import unread_badge_payload
                badge = unread_badge_payload(request.user)
                unread = badge.get('tab_plus') or 0
                unread_dot = bool(badge.get('tab_dot'))
                try:
                    from core.genius.notifications import unread_notification_count
                    genius_unread = unread_notification_count(request.user)
                except Exception:
                    genius_unread = 0
                    genius_unread = 0
                stats, _ = UserStats.objects.get_or_create(user=request.user)
                user_xp = int(stats.xp_total or 0)
                user_xp_tier = max(1, user_xp // 1000 + 1)
                user_xp_tier_pct = min(100, round((user_xp % 1000) / 10))
        except Exception:
            pass
    subject_labels = {
        'maths': 'Maths',
        'physique': 'Physique',
        'chimie': 'Chimie',
        'svt': 'SVT',
        'francais': 'Kreyòl',
        'philosophie': 'Philosophie',
        'anglais': 'Anglais',
        'histoire': 'Sc Social',
        'economie': 'Économie',
        'informatique': 'Informatique',
        'art': 'Art',
        'espagnol': 'Espagnol',
    }
    # Fallback : cookie éventuel envoyé par le JS
    if lang == 'fr':
        lang = request.COOKIES.get('bacia_lang', 'fr')
    if lang not in ('fr', 'kr'):
        lang = 'fr'
    foreign_lang = 'anglais'
    coach_name = ''
    coach_name_required = False
    coach_nav_label = 'Chat IA'
    if profile is not None:
        foreign_lang = profile.langue_etrangere or 'anglais'
        coach_name = (profile.coach_name or '').strip()
        coach_name_required = not coach_name
        if coach_name:
            coach_nav_label = coach_name if len(coach_name) <= 10 else 'Chat IA'
    return {
        'spa_mode': spa_mode,
        'user_lang': lang,
        'profile': profile,
        'foreign_lang': foreign_lang,
        'coach_name': coach_name,
        'coach_nav_label': coach_nav_label,
        'coach_name_required': coach_name_required,
        'unread_msg_count': unread,
        'unread_msg_dot': unread_dot,
        'friend_aliases': friend_aliases,
        'friend_aliases_json': json.dumps(
            {str(k): v for k, v in (friend_aliases or {}).items()}
        ).replace('<', '\\u003c'),
        'firebase_web_config_json': json.dumps(getattr(settings, 'FIREBASE_WEB_CONFIG', {}) or {}).replace('<', '\\u003c'),
        'firebase_vapid_key': getattr(settings, 'FIREBASE_VAPID_KEY', ''),
        'genius_unread_count': genius_unread,
        'user_xp': user_xp,
        'user_streak': user_streak,
        'user_xp_tier': user_xp_tier,
        'user_xp_tier_pct': user_xp_tier_pct,
        'subject_labels_json': json.dumps(subject_labels).replace('<', '\\u003c'),
    }
