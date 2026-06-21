from django.contrib import admin
from .models import QuizQuestion, QuizSession, ChatMessage, UserStats, SubjectMastery, ChatSessionSummary, LearningEvent

@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display  = ('subject', 'enonce', 'sujet')
    list_filter   = ('subject',)
    search_fields = ('enonce', 'sujet')

@admin.register(QuizSession)
class QuizSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'subject', 'score', 'total', 'completed_at')

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'subject', 'created_at')

@admin.register(UserStats)
class UserStatsAdmin(admin.ModelAdmin):
    list_display = ('user', 'quiz_completes', 'exercices_resolus', 'messages_envoyes', 'total_points')

@admin.register(SubjectMastery)
class SubjectMasteryAdmin(admin.ModelAdmin):
    list_display = ('user', 'subject', 'mastery_score', 'confidence_level', 'correct_count', 'error_count', 'last_updated')
    list_filter  = ('subject', 'confidence_level')
    search_fields = ('user__username',)
    readonly_fields = ('last_updated',)

@admin.register(ChatSessionSummary)
class ChatSessionSummaryAdmin(admin.ModelAdmin):
    list_display = ('user', 'session_key', 'subjects_covered', 'message_count', 'created_at')
    list_filter  = ('created_at',)
    search_fields = ('user__username', 'session_key')

@admin.register(LearningEvent)
class LearningEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'event_type', 'subject', 'score_pct', 'created_at')
    list_filter  = ('event_type', 'subject')
    search_fields = ('user__username',)
