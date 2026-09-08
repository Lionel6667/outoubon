from django.contrib import admin

from .models import (
    GeniusBracketNode,
    GeniusCompetition,
    GeniusInvitation,
    GeniusJoinRequest,
    GeniusMatch,
    GeniusMatchEvent,
    GeniusMatchPlayer,
    GeniusMatchQuestion,
    GeniusMembership,
    GeniusNotification,
    GeniusRegistration,
    GeniusTeam,
    GeniusTeamStats,
    GeniusVote,
)


@admin.register(GeniusTeam)
class GeniusTeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'captain', 'invite_code', 'member_count_display', 'is_open_for_requests', 'is_active', 'created_at')
    search_fields = ('name', 'invite_code', 'captain__username')
    readonly_fields = ('invite_code', 'created_at', 'updated_at')

    def member_count_display(self, obj):
        return obj.member_count()
    member_count_display.short_description = 'Membres'


@admin.register(GeniusMembership)
class GeniusMembershipAdmin(admin.ModelAdmin):
    list_display = ('team', 'user', 'role', 'status', 'consecutive_missed', 'removal_eligible', 'captain_eligible')
    list_filter = ('role', 'status')


@admin.register(GeniusJoinRequest)
class GeniusJoinRequestAdmin(admin.ModelAdmin):
    list_display = ('team', 'from_user', 'status', 'created_at', 'responded_at')
    list_filter = ('status',)


@admin.register(GeniusInvitation)
class GeniusInvitationAdmin(admin.ModelAdmin):
    list_display = ('team', 'from_user', 'to_user', 'status', 'created_at', 'expires_at')


@admin.register(GeniusCompetition)
class GeniusCompetitionAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'start_date', 'max_teams', 'created_at')
    list_filter = ('status',)


@admin.register(GeniusRegistration)
class GeniusRegistrationAdmin(admin.ModelAdmin):
    list_display = ('competition', 'team', 'status', 'roster_locked_at', 'registered_at')


@admin.register(GeniusBracketNode)
class GeniusBracketNodeAdmin(admin.ModelAdmin):
    list_display = ('competition', 'phase_key', 'team_a', 'team_b', 'winner_team', 'round_order')


@admin.register(GeniusMatch)
class GeniusMatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'team_a', 'team_b', 'status', 'phase', 'team_a_score', 'team_b_score', 'winner_team')
    list_filter = ('status', 'phase')


admin.site.register(GeniusMatchQuestion)
admin.site.register(GeniusMatchPlayer)
admin.site.register(GeniusVote)
admin.site.register(GeniusMatchEvent)


@admin.register(GeniusTeamStats)
class GeniusTeamStatsAdmin(admin.ModelAdmin):
    list_display = ('team', 'wins', 'losses', 'ranking_points', 'matches_played')


@admin.register(GeniusNotification)
class GeniusNotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'notification_type', 'title', 'read_at', 'created_at')
    list_filter = ('notification_type', 'read_at')
