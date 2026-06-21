"""
Context processor: injecte user_lang, profile, unread_msg_count dans tous les templates.
"""
from accounts.models import UserProfile, FriendMessage, AdminMessage, GroupMessageNotification


def user_lang(request):
    """
    Injecte {{ user_lang }} ('fr' ou 'kr') et {{ profile }} dans tous les contextes de template.
    """
    lang = 'fr'
    profile = None
    unread = 0
    if request.user.is_authenticated:
        try:
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            lang = profile.preferred_lang or 'fr'
            unread = FriendMessage.objects.filter(receiver=request.user, is_read=False, is_system=False).count()
            unread += AdminMessage.objects.filter(receiver=request.user, is_read=False).count()
            unread += GroupMessageNotification.objects.filter(recipient=request.user, is_read=False).count()
        except Exception:
            pass
    # Fallback : cookie éventuel envoyé par le JS
    if lang == 'fr':
        lang = request.COOKIES.get('bacia_lang', 'fr')
    if lang not in ('fr', 'kr'):
        lang = 'fr'
    foreign_lang = 'anglais'
    if profile is not None:
        foreign_lang = profile.langue_etrangere or 'anglais'
    return {
        'user_lang': lang,
        'profile': profile,
        'foreign_lang': foreign_lang,
        'unread_msg_count': unread,
    }
