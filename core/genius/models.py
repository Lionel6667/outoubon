"""
Modèles Groupe de Génies — équipes, concours, matchs synchronisés.
"""
from __future__ import annotations

import secrets
from django.conf import settings
from django.db import models
from django.utils import timezone

from .constants import (
    ABSENCE_THRESHOLD_FOR_REMOVAL,
    COMPETITION_STATUS,
    COMPETITION_STATUS_LABELS,
    DEFAULT_COMPETITION_CONFIG,
    DEFAULT_MATCH_CONFIG,
    INVITE_CODE_PREFIX,
    INVITATION_STATUS,
    MATCH_PHASE,
    MATCH_STATUS,
    MAX_TEAM_SIZE,
)


class GeniusTeam(models.Model):
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=240, blank=True, default='')
    emblem_emoji = models.CharField(max_length=8, blank=True, default='🧠')
    invite_code = models.CharField(max_length=12, unique=True, db_index=True)
    captain = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='genius_teams_captained',
    )
    is_active = models.BooleanField(default=True)
    is_open_for_requests = models.BooleanField(
        default=True,
        help_text='Équipe visible : les joueurs peuvent demander à rejoindre.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @staticmethod
    def generate_invite_code() -> str:
        while True:
            suffix = secrets.token_hex(2).upper()
            code = f'{INVITE_CODE_PREFIX}-{suffix}'
            if not GeniusTeam.objects.filter(invite_code=code).exists():
                return code

    def member_count(self) -> int:
        return self.memberships.filter(status='active').count()

    def is_full(self) -> bool:
        return self.member_count() >= MAX_TEAM_SIZE


class GeniusMembership(models.Model):
    ROLE_CHOICES = [('captain', 'Capitaine'), ('member', 'Membre')]
    STATUS_CHOICES = [
        ('active', 'Actif'),
        ('left', 'Parti'),
        ('removed', 'Retiré'),
    ]

    team = models.ForeignKey(GeniusTeam, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='genius_memberships')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='member')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    consecutive_missed = models.PositiveSmallIntegerField(default=0)
    removal_eligible = models.BooleanField(default=False)
    captain_eligible = models.BooleanField(
        default=True,
        help_text='False si l\'ancien capitaine a transféré son rôle — ne peut plus être capitaine.',
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('team', 'user')]
        ordering = ['joined_at']

    def __str__(self):
        return f'{self.user_id} → {self.team.name} ({self.role})'


class GeniusInvitation(models.Model):
    team = models.ForeignKey(GeniusTeam, on_delete=models.CASCADE, related_name='invitations')
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='genius_invites_sent',
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='genius_invites_received',
    )
    status = models.CharField(max_length=12, choices=[(s, s) for s in INVITATION_STATUS], default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def is_expired(self) -> bool:
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        return False


class GeniusJoinRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('accepted', 'Acceptée'),
        ('rejected', 'Refusée'),
        ('cancelled', 'Annulée'),
    ]

    team = models.ForeignKey(GeniusTeam, on_delete=models.CASCADE, related_name='join_requests')
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='genius_join_requests_sent',
    )
    message = models.CharField(max_length=200, blank=True, default='')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['team', 'status']),
            models.Index(fields=['from_user', 'status']),
        ]

    def __str__(self):
        return f'{self.from_user_id} → {self.team.name} ({self.status})'


class GeniusCompetition(models.Model):
    STATUS_CHOICES = [(s, COMPETITION_STATUS_LABELS.get(s, s)) for s in COMPETITION_STATUS]

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    config = models.JSONField(default=dict, blank=True)
    prizes = models.JSONField(default=dict, blank=True)
    subjects = models.JSONField(default=list, blank=True)
    max_teams = models.PositiveIntegerField(default=32)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='genius_competitions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date', '-created_at']

    def __str__(self):
        return self.name

    def get_config(self) -> dict:
        base = dict(DEFAULT_COMPETITION_CONFIG)
        if isinstance(self.config, dict):
            base.update(self.config)
        return base


class GeniusRegistration(models.Model):
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('registered', 'Inscrite'),
        ('roster_locked', 'Équipe verrouillée'),
        ('eliminated', 'Éliminée'),
        ('withdrawn', 'Retirée'),
    ]

    competition = models.ForeignKey(
        GeniusCompetition, on_delete=models.CASCADE, related_name='registrations',
    )
    team = models.ForeignKey(GeniusTeam, on_delete=models.CASCADE, related_name='competition_regs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    roster_locked_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    eliminated_at = models.DateTimeField(null=True, blank=True)
    current_phase_key = models.CharField(max_length=40, blank=True, default='')

    class Meta:
        unique_together = [('competition', 'team')]
        ordering = ['registered_at']

    def roster_is_locked(self) -> bool:
        return self.roster_locked_at is not None or self.status == 'roster_locked'


class GeniusBracketNode(models.Model):
    competition = models.ForeignKey(
        GeniusCompetition, on_delete=models.CASCADE, related_name='bracket_nodes',
    )
    phase_key = models.CharField(max_length=40, db_index=True)
    phase_label = models.CharField(max_length=80, blank=True, default='')
    round_order = models.PositiveSmallIntegerField(default=0)
    position = models.PositiveSmallIntegerField(default=0)
    team_a = models.ForeignKey(
        GeniusTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='bracket_as_a',
    )
    team_b = models.ForeignKey(
        GeniusTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='bracket_as_b',
    )
    winner_team = models.ForeignKey(
        GeniusTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='bracket_wins',
    )
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children',
    )
    match = models.OneToOneField(
        'GeniusMatch', on_delete=models.SET_NULL, null=True, blank=True, related_name='bracket_node',
    )

    class Meta:
        ordering = ['round_order', 'position']


class GeniusMatch(models.Model):
    STATUS_CHOICES = [(s, s) for s in MATCH_STATUS]
    PHASE_CHOICES = [(p, p) for p in MATCH_PHASE]

    competition = models.ForeignKey(
        GeniusCompetition, on_delete=models.CASCADE, related_name='matches', null=True, blank=True,
    )
    team_a = models.ForeignKey(GeniusTeam, on_delete=models.CASCADE, related_name='matches_as_a')
    team_b = models.ForeignKey(GeniusTeam, on_delete=models.CASCADE, related_name='matches_as_b')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    phase = models.CharField(max_length=20, choices=PHASE_CHOICES, default='waiting')
    config = models.JSONField(default=dict, blank=True)
    team_a_score = models.IntegerField(default=0)
    team_b_score = models.IntegerField(default=0)
    current_question_index = models.PositiveSmallIntegerField(default=0)
    responding_team = models.ForeignKey(
        GeniusTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='active_matches',
    )
    phase_deadline = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    forfeit_team = models.ForeignKey(
        GeniusTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='forfeit_matches',
    )
    forfeit_reason = models.CharField(max_length=80, blank=True, default='')
    winner_team = models.ForeignKey(
        GeniusTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='match_wins',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def get_config(self) -> dict:
        base = dict(DEFAULT_MATCH_CONFIG)
        if isinstance(self.config, dict):
            base.update(self.config)
        if self.competition_id:
            comp_cfg = self.competition.get_config().get('match', {})
            base.update(comp_cfg)
        return base


class GeniusMatchQuestion(models.Model):
    QUESTION_TYPES = [('team', 'Équipe'), ('individual', 'Individuel')]

    match = models.ForeignKey(GeniusMatch, on_delete=models.CASCADE, related_name='questions')
    index = models.PositiveSmallIntegerField()
    subject = models.CharField(max_length=50, blank=True, default='')
    question_type = models.CharField(max_length=12, choices=QUESTION_TYPES, default='team')
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='genius_target_questions',
    )
    responding_team = models.ForeignKey(
        GeniusTeam, on_delete=models.CASCADE, related_name='match_questions',
    )
    question_data = models.JSONField(default=dict)
    correct_choice = models.CharField(max_length=4, blank=True, default='')
    final_choice = models.CharField(max_length=4, blank=True, default='')
    is_correct = models.BooleanField(default=False)
    points_awarded = models.IntegerField(default=0)
    revealed = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('match', 'index')]
        ordering = ['index']


class GeniusMatchPlayer(models.Model):
    match = models.ForeignKey(GeniusMatch, on_delete=models.CASCADE, related_name='players')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    team = models.ForeignKey(GeniusTeam, on_delete=models.CASCADE)
    present = models.BooleanField(default=False)
    marked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('match', 'user')]


class GeniusVote(models.Model):
    match_question = models.ForeignKey(
        GeniusMatchQuestion, on_delete=models.CASCADE, related_name='votes',
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    team = models.ForeignKey(GeniusTeam, on_delete=models.CASCADE)
    choice = models.CharField(max_length=4)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('match_question', 'user')]


class GeniusMatchEvent(models.Model):
    match = models.ForeignKey(GeniusMatch, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=40, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    team = models.ForeignKey(GeniusTeam, on_delete=models.SET_NULL, null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class GeniusTeamStats(models.Model):
    team = models.OneToOneField(GeniusTeam, on_delete=models.CASCADE, related_name='stats')
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    draws = models.PositiveIntegerField(default=0)
    ranking_points = models.PositiveIntegerField(default=0)
    matches_played = models.PositiveIntegerField(default=0)
    total_score = models.PositiveIntegerField(default=0)
    team_streak = models.PositiveIntegerField(default=0, help_text='Victoires consécutives.')
    team_xp = models.PositiveIntegerField(default=0, help_text='XP équipe (lien progression OU TOU BON).')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-ranking_points', '-wins', '-total_score']

    def __str__(self):
        return f'{self.team.name} — {self.wins}V/{self.losses}D'


class GeniusNotification(models.Model):
    NOTIFICATION_TYPES = [
        ('invite', 'Invitation équipe'),
        ('challenge', 'Défi reçu'),
        ('match_ready', 'Match à jouer'),
        ('match_result', 'Résultat match'),
        ('competition_start', 'Concours lancé'),
        ('bracket_ready', 'Bracket publié'),
        ('join_request', 'Demande de rejoindre'),
        ('join_request_accepted', 'Demande acceptée'),
        ('captain_transfer', 'Transfert capitaine'),
        ('match_reminder', 'Rappel match'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='genius_notifications',
    )
    notification_type = models.CharField(max_length=24, choices=NOTIFICATION_TYPES, db_index=True)
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=280, blank=True, default='')
    link_path = models.CharField(max_length=200, blank=True, default='')
    payload = models.JSONField(default=dict, blank=True)
    team = models.ForeignKey(GeniusTeam, on_delete=models.SET_NULL, null=True, blank=True)
    match = models.ForeignKey(GeniusMatch, on_delete=models.SET_NULL, null=True, blank=True)
    competition = models.ForeignKey(GeniusCompetition, on_delete=models.SET_NULL, null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'read_at', '-created_at']),
        ]

    def mark_read(self):
        if not self.read_at:
            self.read_at = timezone.now()
            self.save(update_fields=['read_at'])
