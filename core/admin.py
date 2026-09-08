from django.contrib import admin
from .models import (
    QuizQuestion,
    QuizSession,
    ChatMessage,
    UserStats,
    XpEvent,
    SubjectMastery,
    ChatSessionSummary,
    LearningEvent,
    AiUsageDaily,
    Post,
    PostComment,
    PostCommentAttachment,
    UserFollow,
    Friendship,
)

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
    list_display = ('user', 'xp_total', 'quiz_completes', 'exercices_resolus', 'messages_envoyes', 'total_points')

@admin.register(XpEvent)
class XpEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'source', 'reference', 'created_at')
    list_filter = ('source',)
    search_fields = ('user__username', 'reference')
    readonly_fields = ('created_at',)

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

@admin.register(AiUsageDaily)
class AiUsageDailyAdmin(admin.ModelAdmin):
    list_display = ('date', 'user', 'guest_key', 'feature', 'api_calls', 'prompt_tokens', 'completion_tokens', 'cost_usd_micro')
    list_filter = ('date', 'feature')
    search_fields = ('user__username', 'guest_key')
    readonly_fields = ('date', 'user', 'guest_key', 'feature', 'api_calls', 'prompt_tokens', 'completion_tokens', 'cache_hit_tokens', 'cost_usd_micro')
    date_hierarchy = 'date'


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'post_type', 'author', 'subject', 'created_at')
    list_filter = ('post_type', 'subject', 'created_at')
    search_fields = ('caption', 'quiz_item_hash', 'author__username')

@admin.register(UserFollow)
class UserFollowAdmin(admin.ModelAdmin):
    list_display = ('follower', 'following', 'created_at')
    search_fields = ('follower__username', 'following__username')

@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ('sender', 'receiver', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('sender__username', 'receiver__username')


@admin.register(PostComment)
class PostCommentAdmin(admin.ModelAdmin):
    list_display = ('id', 'post', 'author', 'sticker', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('text', 'sticker', 'author__username')


@admin.register(PostCommentAttachment)
class PostCommentAttachmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'comment', 'attach_type', 'created_at')
    list_filter = ('attach_type', 'created_at')
