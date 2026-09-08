"""Rappels planifiés : série, inactivité, missions, Premium, plan, fiches."""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from accounts.models import PushDevice, PushReceipt, UserProfile
from core.push import send_to_users

User = get_user_model()


def _claim(user_id: int, kind: str, day) -> bool:
    _, created = PushReceipt.objects.get_or_create(
        user_id=user_id, kind=kind, day=day,
    )
    return created


class Command(BaseCommand):
    help = 'Envoie les rappels push du jour (à lancer ~18h Port-au-Prince).'

    def handle(self, *args, **options):
        today = timezone.localdate()
        now = timezone.now()
        user_ids = set(
            PushDevice.objects.filter(enabled=True).values_list('user_id', flat=True)
        )
        if not user_ids:
            self.stdout.write('Aucun appareil push.')
            return

        profiles = {
            p.user_id: p
            for p in UserProfile.objects.filter(user_id__in=user_ids).only(
                'user_id', 'streak', 'last_activity', 'last_seen_at',
                'plan_expiration', 'coach_name',
            )
        }
        sent = 0
        sent += self._streak_and_inactive(user_ids, profiles, today, now)
        sent += self._premium(user_ids, profiles, today)
        sent += self._missions(user_ids, profiles, today)
        sent += self._mistakes(user_ids, today)
        sent += self._plan(user_ids, today)
        sent += self._coach_quiet(user_ids, today)
        self.stdout.write(self.style.SUCCESS(f'Push digest: {sent} envois.'))

    def _streak_and_inactive(self, user_ids, profiles, today, now):
        n = 0
        for uid in user_ids:
            p = profiles.get(uid)
            if not p:
                continue
            last_act = p.last_activity
            streak = int(p.streak or 0)
            if last_act != today and streak >= 2:
                if _claim(uid, 'streak_risk', today):
                    n += send_to_users(
                        [uid],
                        title='Ta série va se briser',
                        body=f'{streak} jours d’affilée. Réviser 5 min aujourd’hui suffit à la garder.',
                        url='/dashboard/',
                        kind='streak_risk',
                    )
                continue
            seen = p.last_seen_at
            if not seen:
                continue
            days = (now - seen).days
            kind = None
            title = body = ''
            if days >= 7:
                kind, title, body = (
                    'inactive_7d',
                    'On ne t’a pas vu depuis 7 jours',
                    'Tes camarades avancent. Reviens 10 minutes — le BAC n’attend pas.',
                )
            elif days >= 3:
                kind, title, body = (
                    'inactive_3d',
                    'On ne t’a pas vu depuis 3 jours',
                    'Ta série et tes missions t’attendent sur OU TOU BON.',
                )
            elif days >= 2:
                kind, title, body = (
                    'inactive_2d',
                    'On ne t’a pas vu depuis 2 jours',
                    'Un petit quiz ce soir et tu restes dans le rythme.',
                )
            if kind and _claim(uid, kind, today):
                n += send_to_users(
                    [uid], title=title, body=body, url='/dashboard/', kind=kind,
                )
        return n

    def _premium(self, user_ids, profiles, today):
        n = 0
        for uid in user_ids:
            p = profiles.get(uid)
            exp = getattr(p, 'plan_expiration', None) if p else None
            if not exp:
                continue
            left = (exp - today).days
            if left == 3 and _claim(uid, 'premium_3d', today):
                n += send_to_users(
                    [uid],
                    title='Premium expire dans 3 jours',
                    body='Renouvelle pour garder le coach, les exercices IA et Extra bèt illimité.',
                    url='/pricing/',
                    kind='premium_3d',
                )
            elif left == 1 and _claim(uid, 'premium_1d', today):
                n += send_to_users(
                    [uid],
                    title='Premium expire demain',
                    body='Dernier jour. Renouvelle pour ne rien perdre.',
                    url='/pricing/',
                    kind='premium_1d',
                )
            elif left == 0 and _claim(uid, 'premium_today', today):
                n += send_to_users(
                    [uid],
                    title='Premium expire aujourd’hui',
                    body='Ton accès Premium se termine ce soir.',
                    url='/pricing/',
                    kind='premium_today',
                )
            elif left == -1 and _claim(uid, 'premium_expired', today):
                n += send_to_users(
                    [uid],
                    title='Premium expiré',
                    body='Tu es revenu au plan gratuit. Renouvelle quand tu veux.',
                    url='/pricing/',
                    kind='premium_expired',
                )
        return n

    def _missions(self, user_ids, profiles, today):
        from core.daily_missions import _activity_today
        n = 0
        for uid in user_ids:
            p = profiles.get(uid)
            if p and p.last_activity == today:
                continue
            if not _claim(uid, 'missions_left', today):
                continue
            try:
                user = User.objects.get(pk=uid)
                act = _activity_today(user)
            except Exception:
                continue
            missing = []
            if act.get('quiz', 0) < 1:
                missing.append('quiz')
            if act.get('exo', 0) < 1:
                missing.append('exercice')
            if act.get('chat', 0) < 1:
                missing.append('coach')
            if not missing or len(missing) == 3:
                body = 'Quiz, exercice et coach t’attendent encore aujourd’hui.'
            else:
                body = 'Il te reste : ' + ', '.join(missing) + '.'
            n += send_to_users(
                [uid],
                title='Missions du jour',
                body=body,
                url='/dashboard/',
                kind='missions_left',
            )
        return n

    def _mistakes(self, user_ids, today):
        from core.models import MistakeTracker
        n = 0
        due = (
            MistakeTracker.objects.filter(
                user_id__in=user_ids, mastered=False, next_review__lte=today,
            )
            .values_list('user_id', flat=True)
            .distinct()
        )
        for uid in due:
            if not _claim(uid, 'mistakes_due', today):
                continue
            n += send_to_users(
                [uid],
                title='Révisions à revoir',
                body='Des erreurs passées sont à retravailler aujourd’hui.',
                url='/dashboard/exercices/',
                kind='mistakes_due',
            )
        return n

    def _plan(self, user_ids, today):
        from core.models import RevisionPlan
        n = 0
        weekday = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'][today.weekday()]
        qs = RevisionPlan.objects.filter(user_id__in=user_ids).order_by('user_id', '-created_at')
        seen = set()
        for plan in qs:
            if plan.user_id in seen:
                continue
            seen.add(plan.user_id)
            content = plan.content or {}
            weeks = content.get('weeks') or []
            has_today = False
            task = ''
            for week in weeks:
                for day in week.get('days') or []:
                    label = (day.get('weekday') or day.get('day') or day.get('label') or '')
                    if weekday.lower() in str(label).lower() or str(today.day) in str(label):
                        has_today = True
                        task = day.get('task') or day.get('subject') or ''
                        break
                if has_today:
                    break
            if not has_today or not _claim(plan.user_id, 'plan_today', today):
                continue
            n += send_to_users(
                [plan.user_id],
                title='Séance du plan aujourd’hui',
                body=(task[:120] if task else 'Ouvre ton plan de révision pour la séance du jour.'),
                url='/dashboard/plan/',
                kind='plan_today',
            )
        return n

    def _coach_quiet(self, user_ids, today):
        from core.models import ChatMessage
        n = 0
        cutoff = timezone.now() - timedelta(days=3)
        recent = set(
            ChatMessage.objects.filter(
                user_id__in=user_ids, role='user', created_at__gte=cutoff,
            ).values_list('user_id', flat=True)
        )
        for uid in user_ids:
            if uid in recent:
                continue
            if not _claim(uid, 'coach_quiet', today):
                continue
            n += send_to_users(
                [uid],
                title='Ton coach t’attend',
                body='Pose-lui une question sur ta matière faible — 5 minutes suffisent.',
                url='/dashboard/chat/',
                kind='coach_quiet',
            )
        return n
