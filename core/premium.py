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
FREE_QUIZ_PER_DAY = 1
FREE_EXERCISE_PER_DAY = 1
FREE_CHAPTERS_PER_SUBJECT = 1
FREE_EXTRA_BET_PER_DAY = 3


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


def can_use_exercise(user):
    if is_premium(user):
        return True, 999
    usage = _get_today_usage(user)
    exo_data = usage.exercise_subjects or {}
    # On additionne toutes les entrées du jour
    total_used = sum(v if isinstance(v, int) else 0 for v in exo_data.values())
    remaining = max(0, FREE_EXERCISE_PER_DAY - total_used)
    return remaining > 0, remaining


def increment_exercise(user, subject='general'):
    usage = _get_today_usage(user)
    exo_data = usage.exercise_subjects or {}
    exo_data[subject] = exo_data.get(subject, 0) + 1
    usage.exercise_subjects = exo_data
    usage.save(update_fields=['exercise_subjects'])


def can_access_chapter(user, subject, chapter_num):
    """Vérifie si le chapitre est accessible (gratuit = premier chapitre seulement).
    chapter_num est 1-based.
    """
    if is_premium(user):
        return True
    return chapter_num <= FREE_CHAPTERS_PER_SUBJECT


def can_use_extra_bet(user):
    """Vérifie si l'utilisateur peut répondre à une question Extra Bète."""
    if is_premium(user):
        return True, 999
    usage = _get_today_usage(user)
    used = getattr(usage, 'extra_bet_count', 0)
    remaining = max(0, FREE_EXTRA_BET_PER_DAY - used)
    return remaining > 0, remaining


def increment_extra_bet(user):
    """Incrémente le compteur Extra Bète."""
    usage = _get_today_usage(user)
    usage.extra_bet_count = getattr(usage, 'extra_bet_count', 0) + 1
    usage.save(update_fields=['extra_bet_count'])


def get_reset_time():
    """Retourne une chaîne indiquant le temps restant avant minuit."""
    from datetime import datetime, timedelta
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    diff = tomorrow - now
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    return f"{hours}h {minutes}mn"


def premium_required_json():
    """Retourne un JsonResponse dict standard pour les endpoints API."""
    return {
        'error': 'premium_required',
        'premium_required': True,
        'message': f'Limite journalière atteinte. Veuillez attendre {get_reset_time()} ou passez au Premium !',
        'upgrade_url': '/pricing/',
    }
