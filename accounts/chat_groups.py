"""Groupes de discussion (OU TOU BON, Génies, groupes créés) + badges non lus."""
from __future__ import annotations

import uuid

from django.contrib.auth.models import User
from django.db.models import Max, Q
from django.utils import timezone

from accounts.models import (
    FriendMessage,
    Friendship,
    GroupMessage,
    GroupMessageNotification,
    StudyChatGroup,
    StudyChatGroupMember,
)

OFFICIAL_SLUG = 'otb-official'
OFFICIAL_NAME = 'Groupe OU TOU BON'


def get_or_create_official_group():
    group, _ = StudyChatGroup.objects.get_or_create(
        slug=OFFICIAL_SLUG,
        defaults={'name': OFFICIAL_NAME, 'kind': StudyChatGroup.KIND_OFFICIAL},
    )
    if group.kind != StudyChatGroup.KIND_OFFICIAL:
        group.kind = StudyChatGroup.KIND_OFFICIAL
        group.name = group.name or OFFICIAL_NAME
        group.save(update_fields=['kind', 'name'])
    return group


def _latest_id_for_group(group):
    qs = GroupMessage.objects.all()
    if group.kind == StudyChatGroup.KIND_OFFICIAL:
        qs = qs.filter(Q(group_id=group.id) | Q(group_id__isnull=True))
    else:
        qs = qs.filter(group_id=group.id)
    return qs.aggregate(m=Max('id')).get('m') or 0


def _ensure_member(group, user, role=StudyChatGroupMember.ROLE_MEMBER):
    member, created = StudyChatGroupMember.objects.get_or_create(
        group=group,
        user=user,
        defaults={'role': role, 'last_read_id': _latest_id_for_group(group)},
    )
    if created is False and role == StudyChatGroupMember.ROLE_OWNER and member.role != role:
        member.role = role
        member.save(update_fields=['role'])
    return member


def sync_genius_group_for_user(user):
    try:
        from core.genius.models import GeniusMembership
        from core.genius.services import get_user_active_team
    except Exception:
        return None
    team = get_user_active_team(user)
    if not team:
        return None
    group, created = StudyChatGroup.objects.get_or_create(
        slug=f'genius-{team.id}',
        defaults={
            'name': f'Génies · {team.name}',
            'kind': StudyChatGroup.KIND_GENIUS,
            'genius_team_id': team.id,
        },
    )
    if group.genius_team_id != team.id or group.kind != StudyChatGroup.KIND_GENIUS:
        group.genius_team_id = team.id
        group.kind = StudyChatGroup.KIND_GENIUS
        group.save(update_fields=['genius_team_id', 'kind'])
    if group.name != f'Génies · {team.name}':
        group.name = f'Génies · {team.name}'
        group.save(update_fields=['name'])

    member_ids = list(
        GeniusMembership.objects.filter(team=team, status='active').values_list('user_id', flat=True)
    )
    existing = set(
        StudyChatGroupMember.objects.filter(group=group).values_list('user_id', flat=True)
    )
    latest = _latest_id_for_group(group)
    to_add = [uid for uid in member_ids if uid not in existing]
    StudyChatGroupMember.objects.bulk_create(
        [
            StudyChatGroupMember(group=group, user_id=uid, last_read_id=latest)
            for uid in to_add
        ],
        ignore_conflicts=True,
    )
    stale = existing.difference(member_ids)
    if stale:
        StudyChatGroupMember.objects.filter(group=group, user_id__in=stale).delete()
    return group


def ensure_user_groups(user):
    official = get_or_create_official_group()
    if user and getattr(user, 'is_authenticated', False):
        _ensure_member(official, user)
        genius = sync_genius_group_for_user(user)
    else:
        genius = None
    return official, genius


def group_messages_qs(group):
    qs = GroupMessage.objects.all()
    if not group or group.kind == StudyChatGroup.KIND_OFFICIAL:
        official = group or get_or_create_official_group()
        return qs.filter(Q(group_id=official.id) | Q(group_id__isnull=True))
    return qs.filter(group_id=group.id)


def user_can_access_group(user, group, write=False):
    if not group:
        return False
    if group.kind == StudyChatGroup.KIND_OFFICIAL:
        if write:
            return bool(user and getattr(user, 'is_authenticated', False))
        return True
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return StudyChatGroupMember.objects.filter(group=group, user=user).exists()


def resolve_group(user, group_id=None, write=False):
    official = get_or_create_official_group()
    if not group_id:
        return official
    try:
        group = StudyChatGroup.objects.get(pk=int(group_id))
    except (TypeError, ValueError, StudyChatGroup.DoesNotExist):
        return None
    if not user_can_access_group(user, group, write=write):
        return None
    return group


def mark_group_read(user, group, last_id=0):
    if not user or not getattr(user, 'is_authenticated', False) or not group:
        return
    member = _ensure_member(group, user)
    last_id = int(last_id or 0)
    if last_id > member.last_read_id:
        member.last_read_id = last_id
        member.save(update_fields=['last_read_id'])
    notif_q = Q(recipient=user, is_read=False)
    if group.kind == StudyChatGroup.KIND_OFFICIAL:
        notif_q &= Q(message__group_id=group.id) | Q(message__group_id__isnull=True)
    else:
        notif_q &= Q(message__group_id=group.id)
    if last_id:
        notif_q &= Q(message_id__lte=last_id)
    GroupMessageNotification.objects.filter(notif_q).update(is_read=True)


def _preview_text(msg, user_id=None):
    if not msg:
        return ''
    prefix = 'Vous : ' if user_id and msg.sender_id == user_id else ''
    if msg.quiz_data:
        body = 'Quiz'
    elif getattr(msg, 'image', None) and msg.image:
        body = 'Photo'
    elif getattr(msg, 'video', None) and msg.video:
        body = 'Vidéo'
    else:
        body = (msg.content or '').strip()
    return (prefix + body)[:80]


def _local_hhmm(dt):
    if not dt:
        return ''
    try:
        from django.utils import timezone as tz
        if timezone.is_aware(dt):
            dt = tz.localtime(dt)
        return dt.strftime('%H:%M')
    except Exception:
        return dt.strftime('%H:%M')


def serialize_group(group, user):
    uid = getattr(user, 'id', None) if user and getattr(user, 'is_authenticated', False) else None
    member = None
    if uid:
        member = StudyChatGroupMember.objects.filter(group=group, user_id=uid).first()
    last_read = member.last_read_id if member else _latest_id_for_group(group)
    qs = group_messages_qs(group)
    last_msg = qs.select_related('sender').order_by('-id').first()
    unread = 0
    tagged = False
    if uid:
        unread = qs.exclude(sender_id=uid).filter(id__gt=last_read).count()
        tag_q = Q(message__group_id=group.id)
        if group.kind == StudyChatGroup.KIND_OFFICIAL:
            tag_q = tag_q | Q(message__group_id__isnull=True)
        tagged = GroupMessageNotification.objects.filter(
            recipient_id=uid, is_read=False
        ).filter(tag_q).exists()
    member_count = StudyChatGroupMember.objects.filter(group=group).count()
    if group.kind == StudyChatGroup.KIND_OFFICIAL:
        member_count = max(member_count, User.objects.filter(is_active=True).count())
    return {
        'id': group.id,
        'name': group.name,
        'kind': group.kind,
        'slug': group.slug,
        'preview': _preview_text(last_msg, uid),
        'time': _local_hhmm(last_msg.created_at) if last_msg else '',
        'unread': unread,
        'tagged': tagged,
        'member_count': member_count,
        'is_official': group.kind == StudyChatGroup.KIND_OFFICIAL,
        'is_genius': group.kind == StudyChatGroup.KIND_GENIUS,
    }


def list_groups_for_user(user):
    official, genius = ensure_user_groups(user)
    groups = [official]
    if genius:
        groups.append(genius)
    if user and getattr(user, 'is_authenticated', False):
        custom_ids = StudyChatGroupMember.objects.filter(
            user=user, group__kind=StudyChatGroup.KIND_CUSTOM
        ).values_list('group_id', flat=True)
        custom = list(StudyChatGroup.objects.filter(id__in=custom_ids).order_by('-created_at'))
        groups.extend(custom)
    seen = set()
    out = []
    for g in groups:
        if not g or g.id in seen:
            continue
        seen.add(g.id)
        out.append(serialize_group(g, user))
    return out


def resolve_invite_users(actor, friend_ids=None, usernames=None):
    friend_ids = friend_ids or []
    usernames = usernames or []
    users = {}
    clean_ids = []
    for raw in friend_ids:
        try:
            clean_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if clean_ids:
        rels = Friendship.objects.filter(
            Q(from_user=actor, to_user_id__in=clean_ids, status='accepted')
            | Q(to_user=actor, from_user_id__in=clean_ids, status='accepted')
        )
        allowed = set()
        for rel in rels:
            other = rel.to_user_id if rel.from_user_id == actor.id else rel.from_user_id
            allowed.add(other)
        for u in User.objects.filter(id__in=allowed, is_active=True):
            users[u.id] = u
    names = [str(n).strip() for n in usernames if str(n).strip()]
    for name in names:
        u = User.objects.filter(username__iexact=name, is_active=True).first()
        if u and u.id != actor.id:
            users[u.id] = u
    users.pop(actor.id, None)
    return list(users.values())


def create_custom_group(actor, name, friend_ids=None, usernames=None):
    name = (name or '').strip()[:80]
    if not name:
        return None, 'Donne un nom au groupe.'
    invitees = resolve_invite_users(actor, friend_ids, usernames)
    if not invitees:
        return None, 'Invite au moins un ami ou un pseudo du site.'
    slug = f'g-{uuid.uuid4().hex[:12]}'
    group = StudyChatGroup.objects.create(
        name=name,
        slug=slug,
        kind=StudyChatGroup.KIND_CUSTOM,
        created_by=actor,
    )
    _ensure_member(group, actor, role=StudyChatGroupMember.ROLE_OWNER)
    latest = _latest_id_for_group(group)
    StudyChatGroupMember.objects.bulk_create(
        [
            StudyChatGroupMember(group=group, user=u, last_read_id=latest)
            for u in invitees
        ],
        ignore_conflicts=True,
    )
    from core.push_events import push_group_invite
    push_group_invite(actor, invitees, group)
    return group, None


def invite_to_group(actor, group, friend_ids=None, usernames=None):
    if not group or group.kind != StudyChatGroup.KIND_CUSTOM:
        return None, 'Impossible d’inviter dans ce groupe.'
    if not StudyChatGroupMember.objects.filter(group=group, user=actor).exists():
        return None, 'Tu ne fais pas partie de ce groupe.'
    invitees = resolve_invite_users(actor, friend_ids, usernames)
    if not invitees:
        return None, 'Aucun utilisateur trouvé.'
    latest = _latest_id_for_group(group)
    added = 0
    new_users = []
    for u in invitees:
        _, created = StudyChatGroupMember.objects.get_or_create(
            group=group, user=u, defaults={'last_read_id': latest}
        )
        if created:
            added += 1
            new_users.append(u)
    if new_users:
        from core.push_events import push_group_invite
        push_group_invite(actor, new_users, group)
    return added, None


def unread_badge_payload(user):
    """Règles icône Messages (tab bar):
    - +N = contacts DM distincts + groupes où on est tagué (dont OTB si tag)
    - point = activité d’un groupe non-officiel sans DM ni tag
    - OTB sans tag: ni point ni +N
    """
    empty = {'count': 0, 'tab_plus': 0, 'tab_dot': False, 'dm_contacts': 0}
    if not user or not getattr(user, 'is_authenticated', False):
        return empty
    dm_contacts = (
        FriendMessage.objects.filter(receiver=user, is_read=False, is_system=False)
        .values('sender_id')
        .distinct()
        .count()
    )
    official = get_or_create_official_group()
    tagged_rows = GroupMessageNotification.objects.filter(
        recipient=user, is_read=False
    ).select_related('message')
    tagged_group_ids = set()
    official_tagged = False
    for row in tagged_rows:
        gid = row.message.group_id
        if gid in (None, official.id):
            official_tagged = True
        else:
            tagged_group_ids.add(gid)
    tab_plus = dm_contacts + (1 if official_tagged else 0) + len(tagged_group_ids)

    tab_dot = False
    if tab_plus == 0:
        memberships = StudyChatGroupMember.objects.filter(user=user).exclude(
            group__kind=StudyChatGroup.KIND_OFFICIAL
        ).select_related('group')
        for mem in memberships:
            qs = group_messages_qs(mem.group).exclude(sender=user).filter(id__gt=mem.last_read_id)
            if qs.exists():
                tab_dot = True
                break
    return {
        'count': tab_plus,
        'tab_plus': tab_plus,
        'tab_dot': tab_dot,
        'dm_contacts': dm_contacts,
    }
