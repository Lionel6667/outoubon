"""
Utilitaires pour vérifier les limites du plan gratuit.

Limites (non-premium) :
  - Chat IA: 2 messages / jour
  - Quiz: 2 / jour
  - Exercices: 1 / matière / mois
  - Cours: 1 chapitre / matière
  - Outils (fiches, plan, favoris, progression): bloqués
  - Extra bèt: bloqué
  - Bibliothèque: visible mais actions bloquées
  - Chat amis: visible mais envoi bloqué
"""

from datetime import date

from accounts.models import DailyUsage, UserProfile

# ── Limites ──
FREE_CHAT_PER_DAY = 2
FREE_QUIZ_PER_DAY = 2
FREE_EXERCISE_PER_SUBJECT_PER_MONTH = 1
FREE_CHAPTERS_PER_SUBJECT = 1


def is_premium(user):
    """Vérifie si l'utilisateur a un abonnement actif."""
    try:
        return user.profile.is_premium
    except (UserProfile.DoesNotExist, AttributeError):
        return False


def _get_today_usage(user):
    usage, _ = DailyUsage.objects.get_or_create(user=user, date=date.today())
    return usage


def can_use_chat(user):
    """Retourne (allowed, remaining)."""
    if is_premium(user):
        return True, 999
    usage = _get_today_usage(user)
    remaining = max(0, FREE_CHAT_PER_DAY - usage.chat_count)
    return remaining > 0, remaining


def increment_chat(user):
    usage = _get_today_usage(user)
    usage.chat_count += 1
    usage.save(update_fields=['chat_count'])


def can_use_quiz(user):
    if is_premium(user):
        return True, 999
    usage = _get_today_usage(user)
    remaining = max(0, FREE_QUIZ_PER_DAY - usage.quiz_count)
    return remaining > 0, remaining


def increment_quiz(user):
    usage = _get_today_usage(user)
    usage.quiz_count += 1
    usage.save(update_fields=['quiz_count'])


def can_use_exercise(user, subject):
    if is_premium(user):
        return True, 999
    usage = _get_today_usage(user)
    # Monthly tracking — use current month key
    month_key = date.today().strftime('%Y-%m')
    exo_data = usage.exercise_subjects or {}
    monthly = exo_data.get(month_key, {})
    used = monthly.get(subject, 0)
    remaining = max(0, FREE_EXERCISE_PER_SUBJECT_PER_MONTH - used)
    return remaining > 0, remaining


def increment_exercise(user, subject):
    usage = _get_today_usage(user)
    month_key = date.today().strftime('%Y-%m')
    exo_data = usage.exercise_subjects or {}
    if month_key not in exo_data:
        exo_data[month_key] = {}
    exo_data[month_key][subject] = exo_data[month_key].get(subject, 0) + 1
    usage.exercise_subjects = exo_data
    usage.save(update_fields=['exercise_subjects'])


def can_access_chapter(user, subject, chapter_num):
    """Vérifie si le chapitre est accessible (gratuit = premier chapitre seulement).
    chapter_num est 1-based.
    """
    if is_premium(user):
        return True
    return chapter_num <= FREE_CHAPTERS_PER_SUBJECT


def premium_required_json():
    """Retourne un JsonResponse dict standard pour les endpoints API."""
    return {
        'error': 'premium_required',
        'premium_required': True,
        'message': 'Cette fonctionnalité nécessite un abonnement premium.',
        'upgrade_url': '/pricing/',
    }
