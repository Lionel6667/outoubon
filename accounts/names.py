"""Noms affichés : alias privé d'un ami, sinon nom public."""
from __future__ import annotations

from accounts.models import FriendAlias


def public_name(user) -> str:
    if not user:
        return 'Utilisateur'
    return (user.get_full_name() or getattr(user, 'username', '') or '').strip() or 'Utilisateur'


def alias_map_for(viewer) -> dict:
    if not viewer or not getattr(viewer, 'is_authenticated', False):
        return {}
    return dict(
        FriendAlias.objects.filter(owner=viewer).values_list('friend_id', 'alias')
    )


def display_name_for(viewer, user, aliases=None) -> str:
    if not user:
        return 'Utilisateur'
    uid = getattr(user, 'id', None)
    if aliases is None:
        aliases = alias_map_for(viewer)
    nick = aliases.get(uid) if uid else None
    if nick:
        return nick
    return public_name(user)


def overlay_alias(aliases, user_id, fallback: str) -> str:
    """Remplace le nom public par l'alias du viewer s'il existe."""
    if not aliases or not user_id:
        return fallback
    return aliases.get(user_id) or fallback
