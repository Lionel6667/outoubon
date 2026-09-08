from datetime import date, datetime, timedelta
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from core import xp_config as C
from core.models import UserStats, XpActivity, XpEvent
from core.xp import (
    activity_monthly_xp_cap,
    award_genius_competition_xp,
    create_activity,
    get_user_xp,
    grant_xp,
    legacy_xp_from_stats,
    reward_exercise,
    seed_legacy_xp_for_user,
    settle_daily_missions,
)
from core.genius.models import GeniusCompetition, GeniusRegistration
from core.genius.services import create_team, join_team_with_code, GeniusError, register_team_for_competition
from core.genius.schedule import ensure_weekly_competition, is_match_window, is_registration_open

User = get_user_model()


class XpEconomyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('xp_user', password='pass12345')
        UserStats.objects.get_or_create(user=self.user)

    def test_grant_100_times_same_reference_once(self):
        amounts = []
        for _ in range(100):
            res = grant_xp(self.user, 5, C.SOURCE_GENIUS, 'genius:same-token', extra={'n': 1})
            amounts.append(res.amount if res.granted else 0)
        self.assertEqual(sum(amounts), 5)
        self.assertEqual(get_user_xp(self.user), 5)

    def test_legacy_seed_preserves_formula(self):
        stats = UserStats.objects.get(user=self.user)
        stats.quiz_completes = 3
        stats.exercices_resolus = 2
        stats.messages_envoyes = 4
        stats.save()
        expected = legacy_xp_from_stats(stats)
        res = seed_legacy_xp_for_user(self.user, stats)
        self.assertTrue(res.granted)
        self.assertEqual(get_user_xp(self.user), expected)
        self.assertFalse(seed_legacy_xp_for_user(self.user, stats).granted)

    def test_rate_100_xp_equals_1_htg(self):
        self.assertEqual(C.XP_VALUE_HTG, 0.01)
        self.assertEqual(C.htg_to_xp(1), 100)
        self.assertEqual(C.xp_to_htg(100), 1.0)
        self.assertEqual(C.htg_to_xp(C.REFERRAL_REWARD_HTG), 15000)

    def test_quiz_save_score_proportional(self):
        act = create_activity(self.user, 'quiz', 'maths', {
            'questions': [
                {'enonce': 'Q1', 'options': ['a', 'b'], 'reponse_correcte': 0},
                {'enonce': 'Q2', 'options': ['a', 'b'], 'reponse_correcte': 1},
            ],
        })
        XpActivity.objects.filter(pk=act.pk).update(created_at=timezone.now() - timedelta(seconds=60))
        c = Client()
        c.force_login(self.user)
        import json
        payload = {
            'subject': 'maths',
            'attempt_id': str(act.token),
            'score': 9999,
            'total': 2,
            'details': [
                {'question': 'Q1', 'chosen': 'a'},
                {'question': 'Q2', 'chosen': 'b'},
            ],
        }
        r1 = c.post('/dashboard/api/quiz/save/', data=json.dumps(payload), content_type='application/json')
        r2 = c.post('/dashboard/api/quiz/save/', data=json.dumps(payload), content_type='application/json')
        # 100% → quiz max + perfect bonus + mission max
        expected = C.XP_QUIZ_MAX + C.XP_QUIZ_PERFECT_BONUS + C.XP_MISSION_QUIZ_MAX
        self.assertEqual(r1.json()['xp_gained'], expected)
        self.assertEqual(r2.json().get('xp_gained', 0), 0)
        self.assertEqual(XpEvent.objects.filter(user=self.user, source=C.SOURCE_QUIZ).count(), 1)

    def test_exercise_idempotent_with_score(self):
        fp = 'abc123sameexo'
        act = create_activity(self.user, 'exercise', 'maths', {'fingerprint': fp})
        XpActivity.objects.filter(pk=act.pk).update(created_at=timezone.now() - timedelta(seconds=60))
        r1 = reward_exercise(self.user, fp, token=act.token, score_pct=1.0)
        r2 = reward_exercise(self.user, fp, token=act.token, score_pct=1.0)
        self.assertTrue(r1.granted)
        self.assertGreaterEqual(r1.amount, C.XP_EXERCISE_MAX)
        self.assertFalse(r2.granted)
        self.assertEqual(XpEvent.objects.filter(user=self.user, source=C.SOURCE_EXERCISE).count(), 1)

    def test_complete_without_token(self):
        r = reward_exercise(self.user, 'nope', token=None)
        self.assertFalse(r.granted)
        self.assertEqual(r.reason, 'no_token')

    def test_missions_idempotent(self):
        from core.models import QuizSession
        QuizSession.objects.create(user=self.user, subject='maths', score=1, total=1, details=[])
        settle_daily_missions(self.user, scores={'quiz': 1.0})
        settle_daily_missions(self.user, scores={'quiz': 1.0})
        n = XpEvent.objects.filter(
            user=self.user, source=C.SOURCE_DAILY_MISSION, reference__contains='daily_quiz',
        ).count()
        self.assertEqual(n, 1)

    def test_streak_once_per_day(self):
        from core.xp import apply_streak_xp
        apply_streak_xp(self.user, 1, date.today())
        apply_streak_xp(self.user, 1, date.today())
        daily = XpEvent.objects.filter(user=self.user, source=C.SOURCE_STREAK, reference__contains='daily')
        self.assertEqual(daily.count(), 1)

    def test_referral_converts_150_htg(self):
        from accounts.models import StudentReferral
        from accounts.referrals import mark_student_referral_paid
        other = User.objects.create_user('filleul', password='pass12345')
        StudentReferral.objects.create(referrer=self.user, referred_user=other, reward_htg=150)
        mark_student_referral_paid(other)
        mark_student_referral_paid(other)
        ev = XpEvent.objects.filter(user=self.user, source=C.SOURCE_REFERRAL)
        self.assertEqual(ev.count(), 1)
        self.assertEqual(ev.first().amount, 15000)
        self.assertEqual(ev.first().extra.get('htg'), 150)

    def test_genius_award_replay(self):
        cap = User.objects.create_user('gcap', password='pass12345')
        mate = User.objects.create_user('gmate', password='pass12345')
        team = create_team(cap, 'T1')
        join_team_with_code(mate, team.invite_code)
        opp_cap = User.objects.create_user('goppcap', password='pass12345')
        opp = create_team(opp_cap, 'T2')
        comp = GeniusCompetition.objects.create(name='C1', status='completed')
        GeniusRegistration.objects.create(competition=comp, team=team, status='roster_locked')
        GeniusRegistration.objects.create(competition=comp, team=opp, status='roster_locked')
        from core.genius.models import GeniusBracketNode
        GeniusBracketNode.objects.create(
            competition=comp, phase_key='final', round_order=3, position=0,
            team_a=team, team_b=opp, winner_team=team,
        )
        n1 = award_genius_competition_xp(comp)
        n2 = award_genius_competition_xp(comp)
        self.assertGreaterEqual(n1, 2)
        self.assertEqual(n2, 0)
        self.assertEqual(get_user_xp(cap), C.GENIUS_XP_CHAMPION)

    def test_monthly_activity_cap_free(self):
        cap = activity_monthly_xp_cap(self.user)
        self.assertEqual(cap, C.ACTIVITY_MONTHLY_XP_CAP_FREE)
        granted = 0
        i = 0
        while granted < cap:
            r = grant_xp(self.user, min(100, cap - granted), C.SOURCE_DAILY_MISSION, f'mission:m-{i}')
            self.assertTrue(r.granted, r.reason)
            granted += r.amount
            i += 1
        r = grant_xp(self.user, 1, C.SOURCE_DAILY_MISSION, 'mission:overflow')
        self.assertFalse(r.granted)
        self.assertEqual(r.reason, 'monthly_activity_cap')
        g = grant_xp(self.user, 50, C.SOURCE_GENIUS, 'genius:extra')
        self.assertTrue(g.granted)

    def test_activity_htg_budget_under_50(self):
        # Premium max activity 4900 XP × 0.01 = 49 HTG
        self.assertLess(C.xp_to_htg(C.ACTIVITY_MONTHLY_XP_CAP_PREMIUM), 50)
        self.assertLessEqual(C.xp_to_htg(C.ACTIVITY_MONTHLY_XP_CAP_FREE), 9)

    def test_course_completion_once(self):
        from types import SimpleNamespace
        from core.xp import grant_course_completion
        sess = SimpleNamespace(chapter_subject='maths', chapter_num=3)
        skip = grant_course_completion(self.user, sess, 0, 4, 5)
        self.assertFalse(skip.granted)
        ok = grant_course_completion(self.user, sess, 3, 4, 5)
        self.assertTrue(ok.granted)
        again = grant_course_completion(self.user, sess, 3, 4, 5)
        self.assertFalse(again.granted)

    def test_weekly_registration_sunday_only(self):
        tz = timezone.get_current_timezone()
        sunday = timezone.make_aware(datetime(2026, 9, 6, 15, 0), tz)
        monday = timezone.make_aware(datetime(2026, 9, 7, 10, 0), tz)
        self.assertTrue(is_registration_open(sunday))
        self.assertFalse(is_registration_open(monday))
        comp_sun = ensure_weekly_competition(now=sunday)
        self.assertEqual(comp_sun.status, 'registration')
        comp_mon = ensure_weekly_competition(now=monday)
        self.assertEqual(comp_mon.status, 'roster_locked')
        cap = User.objects.create_user('wcap', password='pass12345')
        team = create_team(cap, 'WeeklyT')
        for i in range(3):
            u = User.objects.create_user(f'wm{i}', password='pass12345')
            join_team_with_code(u, team.invite_code)
        with patch('core.genius.schedule.is_registration_open', return_value=False):
            with self.assertRaises(GeniusError) as cm:
                register_team_for_competition(cap, comp_mon.pk, team.pk)
            self.assertEqual(cm.exception.code, 'registration_sunday_only')

    def test_match_window_19_20(self):
        tz = timezone.get_current_timezone()
        ok = timezone.make_aware(datetime(2026, 9, 8, 19, 30), tz)
        bad = timezone.make_aware(datetime(2026, 9, 8, 18, 30), tz)
        self.assertTrue(is_match_window(ok))
        self.assertFalse(is_match_window(bad))
