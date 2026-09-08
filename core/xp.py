"""
Ledger XP : attribution idempotente, soldes, missions, concours, parrainage.

Activité (quiz/exo/cours) : pas de rente — XP seulement via missions, proportionnel à la note,
sous plafond mensuel HTG/XP (premium < 50 HTG, gratuit < 10 HTG une fois le taux fixé).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from core import xp_config as C


@dataclass
class GrantResult:
    granted: bool
    amount: int
    balance: int
    reason: str = ''
    event_id: Optional[int] = None


def legacy_xp_from_stats(stats) -> int:
    return (
        int(stats.quiz_completes or 0) * 20
        + int(stats.exercices_resolus or 0) * 50
        + int(stats.messages_envoyes or 0) * 5
    )


def get_user_xp(user) -> int:
    from core.models import UserStats
    stats = UserStats.objects.filter(user=user).only('xp_total').first()
    if stats is None:
        return 0
    return int(stats.xp_total or 0)


def _local_day_start():
    now = timezone.localtime()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _local_month_start():
    now = timezone.localtime()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _count_today(user, source: str) -> int:
    from core.models import XpEvent
    start = _local_day_start()
    return XpEvent.objects.filter(user=user, source=source, created_at__gte=start).count()


def _is_premium(user) -> bool:
    try:
        from core.premium import is_premium
        return bool(is_premium(user))
    except Exception:
        return False


def activity_monthly_xp_cap(user) -> int:
    """Plafond XP activité du mois (filet XP + budget HTG si taux défini)."""
    premium = _is_premium(user)
    xp_cap = C.ACTIVITY_MONTHLY_XP_CAP_PREMIUM if premium else C.ACTIVITY_MONTHLY_XP_CAP_FREE
    htg_budget = (
        C.ACTIVITY_MONTHLY_HTG_BUDGET_PREMIUM if premium else C.ACTIVITY_MONTHLY_HTG_BUDGET_FREE
    )
    if C.XP_VALUE_HTG is not None and float(C.XP_VALUE_HTG) > 0:
        from_htg = C.htg_to_xp(htg_budget)
        if from_htg > 0:
            xp_cap = min(xp_cap, from_htg)
    return max(0, int(xp_cap))


def activity_xp_this_month(user) -> int:
    from core.models import XpEvent
    start = _local_month_start()
    total = (
        XpEvent.objects.filter(
            user=user,
            source__in=C.ACTIVITY_SOURCES,
            created_at__gte=start,
            amount__gt=0,
        ).aggregate(s=Sum('amount'))['s']
        or 0
    )
    return int(total)


def grant_xp(
    user,
    amount: int,
    source: str,
    reference: str,
    extra=None,
    daily_cap: Optional[int] = None,
    allow_zero: bool = False,
) -> GrantResult:
    """
    Crée un XpEvent unique (user, source, reference).
    Replay → granted=False, reason=duplicate.
    """
    from core.models import UserStats, XpEvent

    amount = int(amount)
    if amount < 0:
        return GrantResult(False, 0, get_user_xp(user), 'negative')
    if amount == 0 and not allow_zero:
        return GrantResult(False, 0, get_user_xp(user), 'zero')

    reference = (reference or '')[:180]
    extra = extra or {}

    with transaction.atomic():
        stats, _ = UserStats.objects.select_for_update().get_or_create(user=user)

        if daily_cap is not None and amount > 0:
            if _count_today(user, source) >= daily_cap:
                return GrantResult(False, 0, int(stats.xp_total or 0), 'daily_cap')

        if amount > 0 and source in C.ACTIVITY_SOURCES:
            used = activity_xp_this_month(user)
            cap = activity_monthly_xp_cap(user)
            if used >= cap:
                return GrantResult(False, 0, int(stats.xp_total or 0), 'monthly_activity_cap')
            if used + amount > cap:
                amount = cap - used
                if amount <= 0:
                    return GrantResult(False, 0, int(stats.xp_total or 0), 'monthly_activity_cap')
                extra = dict(extra)
                extra['capped_to_monthly'] = True

        try:
            with transaction.atomic():
                event = XpEvent.objects.create(
                    user=user,
                    amount=amount,
                    source=source,
                    reference=reference,
                    extra=extra,
                )
                if amount:
                    UserStats.objects.filter(pk=stats.pk).update(xp_total=F('xp_total') + amount)
                    stats.refresh_from_db(fields=['xp_total'])
                return GrantResult(True, amount, int(stats.xp_total or 0), 'ok', event.pk)
        except IntegrityError:
            stats.refresh_from_db(fields=['xp_total'])
            return GrantResult(False, 0, int(stats.xp_total or 0), 'duplicate')


def seed_legacy_xp_for_user(user, stats) -> GrantResult:
    amount = legacy_xp_from_stats(stats)
    if amount <= 0:
        return GrantResult(False, 0, int(getattr(stats, 'xp_total', 0) or 0), 'zero')
    return grant_xp(
        user,
        amount,
        C.SOURCE_LEGACY,
        f'legacy:{user.pk}',
        extra={'quiz_completes': stats.quiz_completes, 'exercices_resolus': stats.exercices_resolus},
    )


def settle_daily_missions(user, scores=None) -> list:
    """
    Récompense chaque mission du jour au plus une fois.
    scores: {'quiz': ratio 0-1, 'exo': ratio 0-1} pour XP proportionnel à la note.
    """
    from core.daily_missions import _activity_today, _today

    activity = _activity_today(user)
    today = _today().isoformat()
    scores = scores or {}
    results = []

    quiz_ratio = scores.get('quiz')
    if quiz_ratio is None and activity['quiz'] >= 1:
        quiz_ratio = _latest_quiz_ratio_today(user)
    exo_ratio = scores.get('exo')

    quiz_done = activity['quiz'] >= 1 or quiz_ratio is not None
    exo_done = activity['exo'] >= 1 or ('exo' in scores)
    chat_done = activity['chat'] >= 1

    mapping = [
        ('daily_quiz', quiz_done, C.XP_MISSION_QUIZ_MAX, quiz_ratio),
        ('daily_exo', exo_done, C.XP_MISSION_EXO_MAX, exo_ratio),
        ('daily_chat', chat_done, C.XP_MISSION_CHAT, 1.0),
    ]
    for mid, done, max_xp, ratio in mapping:
        if not done:
            continue
        if mid == 'daily_chat':
            amount = int(max_xp)
        else:
            # Sans note connue : petit montant fixe minimal si activité réelle
            if ratio is None:
                amount = 1 if max_xp > 0 else 0
            else:
                amount = C.xp_from_score(max_xp, ratio=ratio)
        if amount <= 0:
            continue
        results.append(grant_xp(
            user, amount, C.SOURCE_DAILY_MISSION, f'mission:{today}:{mid}',
            extra={'mission_id': mid, 'score_ratio': ratio, 'max_xp': max_xp},
        ))

    from core.daily_missions import _pick_bonus_mission
    from accounts.models import UserProfile
    profile = UserProfile.objects.filter(user=user).first()
    pick = _pick_bonus_mission(user, profile, activity, None, None, activity.get('mistakes_due') or 0)
    if pick and pick.get('completed'):
        bonus_max = min(int(pick.get('xp') or 0), C.XP_MISSION_BONUS_MAX) or C.XP_MISSION_BONUS_MAX
        # Bonus : proportionnel à la meilleure note du jour si dispo
        br = quiz_ratio if quiz_ratio is not None else exo_ratio
        amount = C.xp_from_score(bonus_max, ratio=br if br is not None else 1.0)
        if amount > 0 and int(pick.get('xp') or 0) > 0:
            results.append(grant_xp(
                user, amount, C.SOURCE_DAILY_MISSION,
                f'mission:{today}:{pick["id"]}',
                extra={'mission_id': pick['id']},
            ))
    return results


def _latest_quiz_ratio_today(user) -> Optional[float]:
    from core.models import QuizSession
    from core.daily_missions import _today
    sess = (
        QuizSession.objects.filter(user=user, completed_at__date=_today())
        .order_by('-completed_at')
        .only('score', 'total')
        .first()
    )
    if not sess:
        return None
    return C.score_ratio(sess.score, sess.total)


def grant_course_completion(user, session, prev_step: int, new_step: int, total_steps: int) -> GrantResult:
    """XP une fois par chapitre, seulement si l'élève n'a pas sauté plus d'une étape."""
    total_steps = int(total_steps or 0)
    if total_steps <= 0:
        return GrantResult(False, 0, get_user_xp(user), 'no_plan')
    last = total_steps - 1
    prev_step = int(prev_step or 0)
    new_step = int(new_step or 0)
    if new_step < last:
        return GrantResult(False, 0, get_user_xp(user), 'not_finished')
    if prev_step < last - 1:
        return GrantResult(False, 0, get_user_xp(user), 'skipped')
    subj = getattr(session, 'chapter_subject', '') or 'general'
    num = getattr(session, 'chapter_num', None) or getattr(session, 'chapter_id', None) or 0
    return grant_xp(
        user, C.XP_COURSE_CHAPTER, C.SOURCE_COURSE,
        f'course:{user.pk}:{subj}:{num}',
        extra={'subject': subj, 'num': num},
        daily_cap=C.DAILY_CAP_COURSE,
    )


def apply_streak_xp(user, streak_value: int, streak_started_on) -> list:
    """Check-in quotidien + jalons 3/7/14/30."""
    from core.daily_missions import _today
    results = []
    if C.XP_STREAK_DAILY <= 0 and not C.XP_STREAK_MILESTONES:
        return results
    day = _today().isoformat()
    if C.XP_STREAK_DAILY > 0:
        results.append(grant_xp(
            user, C.XP_STREAK_DAILY, C.SOURCE_STREAK, f'streak:daily:{user.pk}:{day}',
            extra={'streak': streak_value},
            daily_cap=C.DAILY_CAP_STREAK,
        ))
    start_key = streak_started_on.isoformat() if streak_started_on else day
    bonus = C.XP_STREAK_MILESTONES.get(int(streak_value or 0))
    if bonus:
        results.append(grant_xp(
            user, bonus, C.SOURCE_STREAK,
            f'streak:ms:{user.pk}:{int(streak_value)}:{start_key}',
            extra={'milestone': streak_value},
        ))
    return results


def grant_referral_for_first_payment(referrer, referred_user, referral_id: int) -> GrantResult:
    """
    Enregistre la créance parrainage (150 HTG). XP = htg_to_xp(150) (= 0 tant que le taux n'est pas fixé).
    Idempotent via reference student-referral:{id}.
    """
    htg = C.REFERRAL_REWARD_HTG
    xp_amount = C.htg_to_xp(htg)
    return grant_xp(
        referrer,
        xp_amount,
        C.SOURCE_REFERRAL,
        f'student-referral:{referral_id}',
        extra={
            'htg': htg,
            'referred_user_id': referred_user.pk,
            'pending_conversion': xp_amount == 0,
        },
        allow_zero=True,
    )


def convert_pending_referral_events() -> int:
    """Quand XP_VALUE_HTG sera défini : crédite le XP manquant des événements pending."""
    from core.models import XpEvent, UserStats
    if C.XP_VALUE_HTG is None or float(C.XP_VALUE_HTG) <= 0:
        return 0
    converted = 0
    qs = XpEvent.objects.filter(source=C.SOURCE_REFERRAL, extra__pending_conversion=True)
    for event in qs:
        htg = (event.extra or {}).get('htg') or C.REFERRAL_REWARD_HTG
        new_amount = C.htg_to_xp(htg)
        delta = new_amount - int(event.amount or 0)
        if delta <= 0:
            extra = dict(event.extra or {})
            extra['pending_conversion'] = False
            event.extra = extra
            event.save(update_fields=['extra'])
            continue
        event.amount = new_amount
        extra = dict(event.extra or {})
        extra['pending_conversion'] = False
        event.extra = extra
        event.save(update_fields=['amount', 'extra'])
        UserStats.objects.filter(user_id=event.user_id).update(xp_total=F('xp_total') + delta)
        converted += 1
    return converted


def _team_member_ids(team) -> list:
    return list(team.memberships.filter(status='active').values_list('user_id', flat=True))


def award_genius_competition_xp(competition) -> int:
    """Récompense finale unique par élève et par concours. Safe à rejouer."""
    from django.contrib.auth import get_user_model
    from core.genius.models import GeniusBracketNode, GeniusRegistration

    User = get_user_model()
    granted = 0
    champion_id = finalist_id = None
    semi_ids = set()

    final_node = (
        GeniusBracketNode.objects.filter(competition=competition)
        .order_by('-round_order', 'position')
        .first()
    )
    if final_node and final_node.winner_team_id:
        champion_id = final_node.winner_team_id
        if final_node.team_a_id and final_node.team_b_id:
            finalist_id = (
                final_node.team_b_id
                if final_node.team_a_id == champion_id
                else final_node.team_a_id
            )
        semi_round = max(0, (final_node.round_order or 0) - 1)
        if semi_round != final_node.round_order:
            for node in GeniusBracketNode.objects.filter(
                competition=competition, round_order=semi_round,
            ):
                for tid in (node.team_a_id, node.team_b_id):
                    if tid and tid not in (champion_id, finalist_id):
                        semi_ids.add(tid)

    regs = GeniusRegistration.objects.filter(
        competition=competition,
        status__in=['registered', 'roster_locked', 'eliminated'],
    ).select_related('team')

    seen_users = set()
    for reg in regs:
        team = reg.team
        members = _team_member_ids(team)
        if team.id == champion_id:
            amount, place = C.GENIUS_XP_CHAMPION, 'champion'
        elif team.id == finalist_id:
            amount, place = C.GENIUS_XP_FINALIST, 'finalist'
        elif team.id in semi_ids:
            amount, place = C.GENIUS_XP_SEMI, 'semi'
        else:
            amount, place = C.GENIUS_XP_PARTICIPATION, 'participation'
        for uid in members:
            if uid in seen_users:
                continue
            seen_users.add(uid)
            try:
                u = User.objects.get(pk=uid)
            except User.DoesNotExist:
                continue
            res = grant_xp(
                u, amount, C.SOURCE_GENIUS,
                f'genius:{competition.pk}:{uid}',
                extra={'place': place, 'competition_id': competition.pk, 'team_id': team.id},
            )
            if res.granted:
                granted += 1
    return granted


def reward_exercise(user, fingerprint: str, token=None, orphan: bool = False, score_pct=None) -> GrantResult:
    """Une empreinte = une récompense d'activité (note proportionnelle) + missions."""
    from django.utils import timezone as tz
    from core.models import XpActivity

    fingerprint = (fingerprint or '')[:64]
    if not fingerprint:
        return GrantResult(False, 0, get_user_xp(user), 'no_fingerprint')

    ratio = None
    if score_pct is not None:
        try:
            v = float(score_pct)
            if v > 10:
                ratio = v / 100.0
            elif v > 1:
                ratio = v / 10.0
            else:
                ratio = v
            ratio = max(0.0, min(1.0, ratio))
        except (TypeError, ValueError):
            ratio = None

    if orphan:
        amount = C.XP_SOLVE_ORPHAN if ratio is None else C.xp_from_score(C.XP_SOLVE_ORPHAN, ratio=ratio or 0.5)
        return grant_xp(
            user, amount, C.SOURCE_EXERCISE,
            f'solve:{user.pk}:{fingerprint}',
            extra={'orphan': True, 'score_ratio': ratio},
            daily_cap=C.DAILY_CAP_SOLVE,
        )

    if not token:
        return GrantResult(False, 0, get_user_xp(user), 'no_token')

    with transaction.atomic():
        act, status = consume_activity(user, token, XpActivity.KIND_EXERCISE, C.EXERCISE_MIN_SECONDS)
        if status == 'too_fast':
            return GrantResult(False, 0, get_user_xp(user), 'too_fast')
        if status in ('invalid_activity',) or act is None:
            return GrantResult(False, 0, get_user_xp(user), 'invalid_activity')
        stored_fp = ((act.payload or {}).get('fingerprint') or '')[:64]
        if stored_fp and stored_fp != fingerprint:
            fingerprint = stored_fp
        amount = C.xp_from_score(C.XP_EXERCISE_MAX, ratio=ratio if ratio is not None else 0.6)
        res = grant_xp(
            user, amount, C.SOURCE_EXERCISE,
            f'exercise:{user.pk}:{fingerprint}',
            extra={'token': str(act.token), 'score_ratio': ratio},
            daily_cap=C.DAILY_CAP_EXERCISE,
        )
        if status != 'already_consumed' and act is not None:
            act.consumed_at = tz.now()
            act.save(update_fields=['consumed_at'])

    mission_results = settle_daily_missions(user, scores={'exo': ratio} if ratio is not None else {'exo': None})
    mission_gained = sum(r.amount for r in mission_results if r.granted)
    total = (res.amount if res.granted else 0) + mission_gained
    if total:
        return GrantResult(True, total, get_user_xp(user), 'ok' if res.granted else 'mission')
    return res


def create_activity(user, kind: str, subject: str, payload: dict):
    from core.models import XpActivity
    return XpActivity.objects.create(
        user=user,
        kind=kind,
        subject=subject or '',
        payload=payload or {},
    )


def consume_activity(user, token, kind: str, min_seconds: int):
    from uuid import UUID
    from core.models import XpActivity
    try:
        token = token if isinstance(token, UUID) else UUID(str(token))
        act = XpActivity.objects.select_for_update().get(token=token, user=user, kind=kind)
    except (XpActivity.DoesNotExist, ValueError, TypeError):
        return None, 'invalid_activity'
    if act.consumed_at:
        return act, 'already_consumed'
    elapsed = (timezone.now() - act.created_at).total_seconds()
    if elapsed < min_seconds:
        return act, 'too_fast'
    return act, 'ok'


def debit_xp(user, amount: int, source: str, reference: str, extra=None) -> GrantResult:
    """Retire des XP du solde (retrait). Idempotent via (user, source, reference)."""
    from core.models import UserStats, XpEvent

    amount = abs(int(amount))
    if amount <= 0:
        return GrantResult(False, 0, get_user_xp(user), 'zero')
    reference = (reference or '')[:180]
    extra = extra or {}
    with transaction.atomic():
        stats, _ = UserStats.objects.select_for_update().get_or_create(user=user)
        bal = int(stats.xp_total or 0)
        if bal < amount:
            return GrantResult(False, 0, bal, 'insufficient')
        try:
            event = XpEvent.objects.create(
                user=user, amount=-amount, source=source,
                reference=reference, extra=extra,
            )
        except IntegrityError:
            stats.refresh_from_db(fields=['xp_total'])
            return GrantResult(False, 0, int(stats.xp_total or 0), 'duplicate')
        UserStats.objects.filter(pk=stats.pk).update(xp_total=F('xp_total') - amount)
        stats.refresh_from_db(fields=['xp_total'])
        return GrantResult(True, -amount, int(stats.xp_total or 0), 'ok', event.pk)


def request_xp_withdrawal(user, htg_amount, moncash: str):
    """Crée une demande de retrait et débite le XP immédiatement."""
    from accounts.models import XpWithdrawal

    try:
        htg = int(htg_amount)
    except (TypeError, ValueError):
        return None, 'invalid_amount'
    if htg < C.MIN_WITHDRAWAL_HTG:
        return None, 'below_minimum'
    xp_needed = C.htg_to_xp(htg)
    if xp_needed <= 0:
        return None, 'rate_unset'
    phone = (moncash or '').strip().replace(' ', '')
    if phone.startswith('509') and not phone.startswith('+'):
        phone = '+' + phone
    if phone and not phone.startswith('+509'):
        digits = ''.join(c for c in phone if c.isdigit())
        phone = '+509' + digits[-8:] if len(digits) >= 8 else phone
    if len(phone) < 8:
        return None, 'invalid_phone'
    if XpWithdrawal.objects.filter(user=user, status='pending').exists():
        return None, 'pending_exists'
    with transaction.atomic():
        w = XpWithdrawal.objects.create(
            user=user, amount_htg=htg, xp_amount=xp_needed, moncash=phone,
        )
        res = debit_xp(
            user, xp_needed, C.SOURCE_WITHDRAWAL, f'xp-withdraw:{w.pk}',
            extra={'htg': htg, 'moncash': phone},
        )
        if not res.granted:
            w.delete()
            return None, res.reason
        return w, 'ok'


def settle_xp_withdrawal(withdrawal, action: str, note: str = '') -> bool:
    """Approuve ou refuse un retrait. Le refus recrédite les XP."""
    from accounts.models import XpWithdrawal

    if withdrawal.status != 'pending':
        return False
    action = (action or '').strip().lower()
    if action == 'approve':
        withdrawal.status = 'approved'
        withdrawal.note = note or 'Approuvé'
        withdrawal.save(update_fields=['status', 'note', 'updated_at'])
        from core.push_events import push_xp_withdrawal
        push_xp_withdrawal(withdrawal.user, True, withdrawal.note)
        return True
    if action == 'reject':
        grant_xp(
            withdrawal.user,
            int(withdrawal.xp_amount or 0),
            C.SOURCE_WITHDRAWAL,
            f'xp-withdraw-refund:{withdrawal.pk}',
            extra={'refund': True, 'htg': withdrawal.amount_htg},
        )
        withdrawal.status = 'rejected'
        withdrawal.note = note or 'Refusé'
        withdrawal.save(update_fields=['status', 'note', 'updated_at'])
        from core.push_events import push_xp_withdrawal
        push_xp_withdrawal(withdrawal.user, False, withdrawal.note)
        return True
    return False
