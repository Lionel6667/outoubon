"""
Signaux Django pour le tracking d'apprentissage et l'adaptation du bandit Thompson Sampling.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import LearningEvent
from core.ml.content_bandit import record_arm_outcome


@receiver(post_save, sender=LearningEvent)
def track_bandit_outcome(sender, instance, created, **kwargs):
    """
    Récompense ou pénalise le bras du bandit après chaque activité complétée.
    """
    if not created or not instance.user:
        return

    arm_map = {
        "quiz_completed": "quiz_court",
        "exercise_corrected": "exercice_srs",
        "flashcard_reviewed": "fiche_memo",
        "course_chapter": "cours_chapitre",
        "quiz": "quiz_court",
        "exercise": "exercice_srs",
    }
    arm = arm_map.get(instance.event_type)
    if not arm:
        return

    # Si l'événement a enregistré un gain de score ou de maîtrise
    learning_gain = bool(getattr(instance, "xp_awarded", 0) > 0 or getattr(instance, "success", True))
    record_arm_outcome(instance.user, arm, learning_gain)
