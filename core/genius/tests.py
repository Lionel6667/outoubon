from django.contrib.auth import get_user_model
from django.test import TestCase

from core.genius.constants import MAX_TEAM_SIZE, MIN_PLAYERS_TO_START
from core.genius.models import GeniusCompetition, GeniusMatch, GeniusMatchPlayer
from core.genius.services import (
    GeniusError,
    build_competition_bracket,
    create_friendly_match,
    create_team,
    join_team_with_code,
    lock_roster,
    register_team_for_competition,
    start_match,
    get_user_active_team,
)

User = get_user_model()


def _user(username):
    return User.objects.create_user(username=username, password='testpass123')


def _fill_team(team, prefix):
    for i in range(MAX_TEAM_SIZE - 1):
        u = _user(f'{prefix}_m{i}')
        team.memberships.create(user=u, role='member', status='active')


class GeniusTeamTests(TestCase):
    def test_create_and_join_team(self):
        captain = _user('cap1')
        team = create_team(captain, 'Les Génies')
        self.assertEqual(team.member_count(), 1)
        self.assertTrue(team.invite_code.startswith('OTB-'))

        mate = _user('mate1')
        joined = join_team_with_code(mate, team.invite_code)
        self.assertEqual(joined.id, team.id)
        self.assertEqual(team.member_count(), 2)

    def test_cannot_join_when_full(self):
        captain = _user('cap2')
        team = create_team(captain, 'Full Team')
        _fill_team(team, 'full')
        self.assertTrue(team.is_full())
        outsider = _user('outsider')
        with self.assertRaises(GeniusError) as ctx:
            join_team_with_code(outsider, team.invite_code)
        self.assertEqual(ctx.exception.code, 'team_full')

    def test_captain_absent_forfeit(self):
        cap_a = _user('cap_a')
        cap_b = _user('cap_b')
        team_a = create_team(cap_a, 'Team A')
        _fill_team(team_a, 'a')
        team_b = create_team(cap_b, 'Team B')
        _fill_team(team_b, 'b')

        match = GeniusMatch.objects.create(
            team_a=team_a,
            team_b=team_b,
            status='scheduled',
            phase='waiting',
        )
        for team in (team_a, team_b):
            for m in team.memberships.filter(status='active'):
                GeniusMatchPlayer.objects.create(match=match, user=m.user, team=team, present=False)

        # Only team B marks presence (including captain)
        for mp in match.players.filter(team=team_b):
            mp.present = True
            mp.save()

        start_match(cap_b, match.id)
        match.refresh_from_db()
        self.assertEqual(match.status, 'forfeit')
        self.assertEqual(match.winner_team_id, team_b.id)
        self.assertEqual(match.forfeit_reason, 'captain_absent')

    def test_active_team_guard(self):
        u = _user('solo')
        create_team(u, 'First')
        with self.assertRaises(GeniusError):
            create_team(u, 'Second')
        self.assertIsNotNone(get_user_active_team(u))

    def test_bracket_four_teams(self):
        comp = GeniusCompetition.objects.create(name='Bracket Test', status='registration')
        for i in range(4):
            cap = _user(f'br_cap_{i}')
            team = create_team(cap, f'Bracket {i}')
            _fill_team(team, f'br{i}')
            register_team_for_competition(cap, comp.id, team.id)
            lock_roster(cap, comp.id, team.id)
        result = build_competition_bracket(comp.id)
        self.assertEqual(result['teams'], 4)
        self.assertEqual(GeniusMatch.objects.filter(competition=comp).count(), 2)
        comp.refresh_from_db()
        self.assertEqual(comp.status, 'in_progress')

    def test_friendly_match(self):
        cap_a = _user('fa_cap')
        cap_b = _user('fb_cap')
        team_a = create_team(cap_a, 'Friendly A')
        _fill_team(team_a, 'fa')
        team_b = create_team(cap_b, 'Friendly B')
        _fill_team(team_b, 'fb')
        match = create_friendly_match(cap_a, team_b.id)
        self.assertIsNone(match.competition_id)
        self.assertEqual(match.players.count(), 8)

    def test_start_with_three_of_four_present(self):
        cap_a = _user('3of4_a')
        cap_b = _user('3of4_b')
        team_a = create_team(cap_a, 'Three A')
        _fill_team(team_a, '3a')
        team_b = create_team(cap_b, 'Three B')
        _fill_team(team_b, '3b')

        match = GeniusMatch.objects.create(team_a=team_a, team_b=team_b, status='scheduled', phase='waiting')
        for team in (team_a, team_b):
            for m in team.memberships.filter(status='active'):
                GeniusMatchPlayer.objects.create(match=match, user=m.user, team=team, present=False)

        extra_ids = list(
            match.players.filter(team=team_a).exclude(user=cap_a).values_list('pk', flat=True)[:2]
        )
        match.players.filter(user=cap_a).update(present=True)
        match.players.filter(pk__in=extra_ids).update(present=True)
        match.players.filter(team=team_b).update(present=True)

        self.assertEqual(match.players.filter(team=team_a, present=True).count(), MIN_PLAYERS_TO_START)

        start_match(cap_a, match.id)
        match.refresh_from_db()
        self.assertEqual(match.status, 'in_progress')
        self.assertGreaterEqual(match.questions.count(), 1)

    def test_invite_notification(self):
        cap = _user('notif_cap')
        mate = _user('notif_mate')
        team = create_team(cap, 'Notif Team')
        from core.genius.services import invite_user
        from core.genius.models import GeniusNotification
        invite_user(cap, team.id, mate.id)
        self.assertTrue(GeniusNotification.objects.filter(user=mate, notification_type='invite').exists())

    def test_stats_after_match(self):
        from core.genius.models import GeniusTeamStats
        cap_a = _user('st_a')
        cap_b = _user('st_b')
        ta = create_team(cap_a, 'Stats A')
        _fill_team(ta, 'sta')
        tb = create_team(cap_b, 'Stats B')
        _fill_team(tb, 'stb')
        match = GeniusMatch.objects.create(
            team_a=ta, team_b=tb, status='finished', phase='finished',
            team_a_score=500, team_b_score=300, winner_team=ta,
        )
        from core.genius.notifications import update_team_stats_from_match
        update_team_stats_from_match(match)
        sa = GeniusTeamStats.objects.get(team=ta)
        sb = GeniusTeamStats.objects.get(team=tb)
        self.assertEqual(sa.wins, 1)
        self.assertEqual(sa.ranking_points, 3)
        self.assertEqual(sb.losses, 1)

    def test_join_request_flow(self):
        cap = _user('jr_cap')
        seeker = _user('jr_seek')
        team = create_team(cap, 'Join Req Team')
        from core.genius.services import request_join_team, respond_join_request
        req = request_join_team(seeker, team.id, 'Salut !')
        self.assertEqual(req.status, 'pending')
        respond_join_request(cap, req.id, True)
        self.assertEqual(team.member_count(), 2)
        self.assertIsNotNone(get_user_active_team(seeker))

    def test_transfer_captain_revokes_old(self):
        cap = _user('tc_cap')
        mate = _user('tc_mate')
        team = create_team(cap, 'Transfer Team')
        join_team_with_code(mate, team.invite_code)
        from core.genius.services import transfer_captain
        transfer_captain(cap, team.id, mate.id)
        team.refresh_from_db()
        self.assertEqual(team.captain_id, mate.id)
        old_mem = team.memberships.get(user=cap)
        self.assertFalse(old_mem.captain_eligible)
        with self.assertRaises(GeniusError):
            transfer_captain(cap, team.id, cap.id)

    def test_empty_join_code_rejected(self):
        u = _user('empty_join')
        with self.assertRaises(GeniusError) as ctx:
            join_team_with_code(u, '')
        self.assertEqual(ctx.exception.code, 'invalid_code')
