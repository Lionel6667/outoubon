from django.contrib import admin, messages
from django.db import transaction

from .models import School, UserProfile


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ('name', 'profile_count', 'created_at')
    search_fields = ('name',)
    ordering = ('name',)
    actions = ['merge_schools_into_first']

    @admin.display(description='Profils')
    def profile_count(self, obj):
        return UserProfile.objects.filter(school=obj.name).count()

    @admin.action(description='Fusionner dans la première école sélectionnée (supprime les doublons)')
    def merge_schools_into_first(self, request, queryset):
        schools = list(queryset.order_by('id'))
        if len(schools) < 2:
            self.message_user(request, 'Sélectionnez au moins 2 écoles à fusionner.', messages.ERROR)
            return
        target = schools[0]
        removed = 0
        updated_profiles = 0
        with transaction.atomic():
            for school in schools[1:]:
                updated_profiles += UserProfile.objects.filter(school=school.name).update(school=target.name)
                school.delete()
                removed += 1
        self.message_user(
            request,
            f'Fusion terminée : « {target.name} » — {removed} doublon(s) supprimé(s), {updated_profiles} profil(s) mis à jour.',
            messages.SUCCESS,
        )
