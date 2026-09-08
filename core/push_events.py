"""Événements push — un helper par type. Jamais d’exception vers l’appelant."""
from __future__ import annotations

from accounts.names import display_name_for, public_name
from core.push import send_to_users


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return 0


def _preview(text: str, n: int = 90) -> str:
    text = (text or '').replace('\n', ' ').strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + '…'


def push_dm(sender, receiver, content: str):
    if not sender or not receiver or sender.id == receiver.id:
        return 0
    name = display_name_for(receiver, sender)
    body = _preview(content) or 'Nouveau message'
    return _safe(
        send_to_users,
        [receiver.id],
        title=name,
        body=body,
        url=f'/dashboard/amis/?chat_with={sender.id}',
        kind='dm',
        collapse_key=f'dm-{sender.id}',
        exclude_ids=[sender.id],
    )


def push_friend_request(from_user, to_user):
    if not from_user or not to_user or from_user.id == to_user.id:
        return 0
    name = display_name_for(to_user, from_user)
    return _safe(
        send_to_users,
        [to_user.id],
        title='Nouvelle demande d’ami',
        body=f'{name} veut t’ajouter.',
        url='/dashboard/amis/',
        kind='friend_request',
        collapse_key='friend-req',
    )


def push_friend_accepted(accepter, requester):
    if not accepter or not requester:
        return 0
    name = display_name_for(requester, accepter)
    return _safe(
        send_to_users,
        [requester.id],
        title='Demande acceptée',
        body=f'{name} a accepté ta demande. Vous pouvez discuter.',
        url=f'/dashboard/amis/?chat_with={accepter.id}',
        kind='friend_accepted',
    )


def push_group_mention(sender, recipients, group, reason: str, content: str):
    ids = [getattr(u, 'id', u) for u in recipients]
    if not ids:
        return 0
    gname = getattr(group, 'name', None) or 'Groupe'
    url = f'/dashboard/amis/?group={getattr(group, "id", "")}'
    if reason == 'reply':
        title = f'{public_name(sender)} a répondu'
        body = _preview(content) or f'dans {gname}'
        kind = 'group_reply'
    elif reason == 'everyone':
        title = f'{gname} · @tout le monde'
        body = f'{public_name(sender)} : {_preview(content) or "a mentionné tout le monde"}'
        kind = 'group_everyone'
    else:
        title = f'{public_name(sender)} t’a mentionné'
        body = _preview(content) or f'dans {gname}'
        kind = 'group_mention'
    return _safe(
        send_to_users,
        ids,
        title=title,
        body=body,
        url=url,
        kind=kind,
        collapse_key=f'grp-{getattr(group, "id", 0)}',
        exclude_ids=[getattr(sender, 'id', None)],
    )


def push_group_invite(actor, invitees, group):
    ids = [getattr(u, 'id', u) for u in invitees]
    gname = getattr(group, 'name', None) or 'un groupe'
    return _safe(
        send_to_users,
        ids,
        title='Invitation dans un groupe',
        body=f'{public_name(actor)} t’a ajouté à {gname}.',
        url=f'/dashboard/amis/?group={getattr(group, "id", "")}',
        kind='group_invite',
        exclude_ids=[getattr(actor, 'id', None)],
    )


def push_admin_announcement(user_ids, content: str):
    return _safe(
        send_to_users,
        user_ids,
        title='Annonce OUTOUBON',
        body=_preview(content, 120) or 'Nouveau message officiel.',
        url='/dashboard/amis/',
        kind='admin_annonce',
        collapse_key='otb-annonce',
    )


def push_extra_bet_like(liker, post):
    if not post or post.user_id == getattr(liker, 'id', None):
        return 0
    name = display_name_for(post.user, liker)
    return _safe(
        send_to_users,
        [post.user_id],
        title='Nouveau like Extra bèt',
        body=f'{name} a aimé ta question.',
        url='/dashboard/extra-bet/',
        kind='extra_bet_like',
        collapse_key=f'eblike-{post.id}',
    )


def push_extra_bet_answer(solver, post, is_correct: bool):
    if not post or post.user_id == getattr(solver, 'id', None):
        return 0
    name = display_name_for(post.user, solver)
    result = 'a trouvé la bonne réponse' if is_correct else 'a tenté ta question'
    return _safe(
        send_to_users,
        [post.user_id],
        title='Extra bèt',
        body=f'{name} {result}.',
        url='/dashboard/extra-bet/',
        kind='extra_bet_answer',
        collapse_key=f'ebans-{post.id}',
    )


def push_match_found(user, opponent):
    name = display_name_for(user, opponent)
    return _safe(
        send_to_users,
        [user.id],
        title='Adversaire trouvé !',
        body=f'Match contre {name}. C’est parti.',
        url='/dashboard/match/',
        kind='match_found',
        collapse_key='match-found',
    )


def push_duel_joined(creator, challenger):
    name = display_name_for(creator, challenger)
    return _safe(
        send_to_users,
        [creator.id],
        title='Duel rejoint',
        body=f'{name} a rejoint ton duel. Ouvre Match.',
        url='/dashboard/duel/',
        kind='duel_joined',
    )


def push_duel_finished(user, opponent, won: bool):
    name = display_name_for(user, opponent) if opponent else 'ton adversaire'
    body = f'Tu as gagné contre {name} !' if won else f'{name} a gagné le duel.'
    return _safe(
        send_to_users,
        [user.id],
        title='Duel terminé',
        body=body,
        url='/dashboard/match/',
        kind='duel_result',
    )


def push_genius(user, title: str, body: str, link_path: str = ''):
    return _safe(
        send_to_users,
        [user.id],
        title=title or 'Groupe de Génies',
        body=_preview(body, 140) or 'Nouvelle alerte Génies.',
        url=link_path or '/dashboard/genius/',
        kind='genius',
        collapse_key='genius',
    )


def push_device_switch(user):
    return _safe(
        send_to_users,
        [user.id],
        title='Nouvel appareil',
        body='Quelqu’un essaie de se connecter à ton compte. Tu as 5 minutes pour confirmer.',
        url='/dashboard/profil/',
        kind='device_switch',
        collapse_key='device',
    )


def push_premium_activated(user, until=None):
    extra = f' jusqu’au {until}' if until else ''
    return _safe(
        send_to_users,
        [user.id],
        title='Premium activé',
        body=f'Ton abonnement est actif{extra}. Merci !',
        url='/dashboard/',
        kind='premium_on',
    )


def push_referral_reward(referrer, referred):
    name = display_name_for(referrer, referred)
    return _safe(
        send_to_users,
        [referrer.id],
        title='Parrainage validé',
        body=f'{name} s’est abonné — ta récompense est créditée.',
        url='/dashboard/gains/',
        kind='referral',
    )


def push_xp_withdrawal(user, approved: bool, note: str = ''):
    if approved:
        title, body = 'Retrait XP approuvé', note or 'Ton retrait a été validé.'
    else:
        title, body = 'Retrait XP refusé', note or 'Tes XP ont été recrédités.'
    return _safe(
        send_to_users,
        [user.id],
        title=title,
        body=body,
        url='/dashboard/gains/',
        kind='xp_withdraw',
    )


def push_streak_milestone(user, streak: int):
    return _safe(
        send_to_users,
        [user.id],
        title=f'Série de {streak} jours !',
        body='Continue comme ça — ne casse pas la chaîne demain.',
        url='/dashboard/',
        kind='streak_milestone',
    )
