"""
Signals pour créer automatiquement le UserProfile et UserStats
après une inscription via Google OAuth (allauth).
"""
from django.dispatch import receiver
from allauth.socialaccount.signals import social_account_added, pre_social_login
from allauth.account.signals import user_signed_up


def _ensure_profile(user, extra_data=None):
    """Crée UserProfile + UserStats si absents, remplit depuis les données Google."""
    from .models import UserProfile
    from core.models import UserStats

    profile, _ = UserProfile.objects.get_or_create(user=user)
    if extra_data:
        if not profile.first_name:
            profile.first_name = extra_data.get('given_name', '')
        if not profile.last_name:
            profile.last_name = extra_data.get('family_name', '')
        profile.save(update_fields=['first_name', 'last_name'])

    changed = False
    if not user.first_name and profile.first_name:
        user.first_name = profile.first_name
        changed = True
    if not user.last_name and profile.last_name:
        user.last_name = profile.last_name
        changed = True
    if changed:
        user.save(update_fields=['first_name', 'last_name'])

    UserStats.objects.get_or_create(user=user)


@receiver(user_signed_up)
def on_user_signed_up(request, user, **kwargs):
    """Déclenché à la première inscription (email ou Google)."""
    sociallogin = kwargs.get('sociallogin')
    extra_data = {}
    if sociallogin:
        extra_data = sociallogin.account.extra_data
    _ensure_profile(user, extra_data)


@receiver(social_account_added)
def on_social_account_added(request, sociallogin, **kwargs):
    """Déclenché quand un compte social est ajouté à un utilisateur existant."""
    _ensure_profile(sociallogin.user, sociallogin.account.extra_data)


@receiver(pre_social_login)
def on_pre_social_login(request, sociallogin, **kwargs):
    """Assure que le profil existe même pour les connexions répétées."""
    if sociallogin.is_existing:
        _ensure_profile(sociallogin.user, sociallogin.account.extra_data)
