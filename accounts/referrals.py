"""Parrainage élève (lien ?ref=Uxxxxxxxx) distinct du programme agent."""
import uuid

from django.utils import timezone

from core.xp_config import REFERRAL_REWARD_HTG


def ensure_invite_code(profile):
    if profile.invite_code:
        return profile.invite_code
    from accounts.models import UserProfile
    for _ in range(12):
        code = 'U' + uuid.uuid4().hex[:8].upper()
        if not UserProfile.objects.filter(invite_code=code).exists():
            profile.invite_code = code
            profile.save(update_fields=['invite_code'])
            return code
    profile.invite_code = 'U' + uuid.uuid4().hex[:10].upper()
    profile.save(update_fields=['invite_code'])
    return profile.invite_code


def store_student_or_agent_ref(request):
    """Session : code agent (hex) ou code élève (préfixe U)."""
    raw = (request.GET.get('ref') or '').strip().upper()
    if not raw:
        return
    if raw.startswith('U'):
        request.session['student_invite_code'] = raw
        request.session.pop('agent_referral_code', None)
        return
    from accounts.models import Agent
    agent = Agent.objects.filter(referral_code=raw, is_active=True).first()
    if agent:
        request.session['agent_referral_code'] = agent.referral_code
        request.session.pop('student_invite_code', None)
        return
    request.session.pop('agent_referral_code', None)


def attach_student_referral(request, user):
    code = (request.session.get('student_invite_code') or '').strip().upper()
    if not code:
        return None
    from accounts.models import StudentReferral, UserProfile
    profile = UserProfile.objects.filter(invite_code=code).select_related('user').first()
    request.session.pop('student_invite_code', None)
    if not profile or profile.user_id == user.id:
        return None
    obj, created = StudentReferral.objects.get_or_create(
        referred_user=user,
        defaults={
            'referrer': profile.user,
            'reward_htg': REFERRAL_REWARD_HTG,
            'paid': False,
        },
    )
    return obj if created else obj


def mark_student_referral_paid(user) -> bool:
    from django.db import transaction
    from accounts.models import StudentReferral
    from core.xp import grant_referral_for_first_payment

    with transaction.atomic():
        rec = (
            StudentReferral.objects.select_for_update()
            .filter(referred_user=user, paid=False)
            .select_related('referrer')
            .first()
        )
        if not rec:
            return False
        rec.paid = True
        rec.paid_at = timezone.now()
        rec.save(update_fields=['paid', 'paid_at'])
        grant_referral_for_first_payment(rec.referrer, user, rec.pk)
        try:
            from core.push_events import push_referral_reward
            push_referral_reward(rec.referrer, user)
        except Exception:
            pass
        return True
