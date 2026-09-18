"""
Moteur dédié à la page Exercices et aux Examens Blancs.
Gère la sélection adaptative CAT, l'enregistrement avec poids renforcé
pour les épreuves officielles et le suivi de récupération FSRS.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from core.models import ItemParameters, MemoryCard, StudentAbility, TopicNode
from core.ml.irt_engine import (
    probability_correct,
    select_next_adaptive_item,
    update_student_theta,
)
from core.ml.bkt_engine import record_response_and_update_mastery
from core.ml.fsrs_engine import (
    RATING_AGAIN,
    RATING_EASY,
    RATING_GOOD,
    RATING_HARD,
    create_card_from_mistake,
    update_card_after_review,
)


def get_exercise_recommendations(
    user, subject: str, serie: str, n: int = 10
) -> List[ItemParameters]:
    """
    Sélection adaptative CAT d'exercices :
    Mix 70% 'au niveau' de theta et 30% de 'challenge' (theta + 0.5).
    """
    candidates = list(
        ItemParameters.objects.filter(
            source_type="exercice",
            topic__subject=subject,
            topic__serie__in=[serie, ""],
        )
    )
    if not candidates:
        return []

    ability = StudentAbility.objects.filter(user=user, subject=subject).first() if user and user.is_authenticated else None
    theta = ability.theta if ability else 0.0

    selected: List[ItemParameters] = []
    used_uids = set()
    n_challenge = max(1, round(n * 0.3))
    n_level = n - n_challenge

    for _ in range(n_level):
        item = select_next_adaptive_item(user, subject, candidates, exclude_uids=used_uids)
        if not item:
            break
        selected.append(item)
        used_uids.add(item.item_uid)

    challenge_candidates = [c for c in candidates if c.item_uid not in used_uids]
    challenge_candidates.sort(key=lambda c: abs(c.difficulty_b - (theta + 0.5)))
    selected.extend(challenge_candidates[:n_challenge])

    return selected


def record_exercise_attempt(
    user,
    subject: str,
    item: ItemParameters,
    topic: TopicNode,
    correct: bool,
    is_examen_blanc: bool = False,
    response_time_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Enregistre une tentative d'exercice ou d'examen blanc.
    Applique un coefficient d'apprentissage x1.8 pour les examens blancs.
    """
    if not user or not user.is_authenticated:
        return {}

    learning_rate = None
    if is_examen_blanc:
        ab = StudentAbility.objects.filter(user=user, subject=subject).first()
        n = ab.n_responses if ab else 0
        base_rate = max(0.05, 0.5 / (1 + n / 10))
        learning_rate = base_rate * 1.8

    ability = update_student_theta(user, subject, item, correct, learning_rate=learning_rate)
    mastery = record_response_and_update_mastery(user, topic, correct, source_format="exercice_calcul")

    # Mise à jour de la carte mémoire FSRS
    card = None
    if not correct:
        card = create_card_from_mistake(user, topic, item_uid=item.item_uid)
    else:
        card = MemoryCard.objects.filter(user=user, topic=topic, item_uid=item.item_uid).first()
        if card:
            if response_time_seconds is not None:
                rating = (
                    RATING_EASY if response_time_seconds < 25 else (
                        RATING_HARD if response_time_seconds > 90 else RATING_GOOD
                    )
                )
            else:
                rating = RATING_GOOD
            card = update_card_after_review(card, rating)

    return {"ability": ability, "mastery": mastery, "card": card}


def compute_recovery_status(card: MemoryCard) -> str:
    """
    Statut à 3 niveaux basé sur la stabilité FSRS :
      - 'mastered'   : stabilité >= 21 jours et aucun échec récent
      - 'recovering' : stabilité en hausse (>= 3 jours) après un échec
      - 'struggling' : échecs répétés (stabilité < 3 jours)
    """
    if card.reps == 0:
        return "not_started"
    if card.lapses == 0 and card.stability >= 21:
        return "mastered"
    if card.lapses > 0 and card.stability < 3:
        return "struggling"
    if card.lapses > 0 and card.stability >= 3:
        return "recovering"
    return "in_progress"


def get_recovery_stats(user, subject: str) -> Dict[str, Any]:
    """
    Calcule le taux de récupération des erreurs FSRS pour une matière.
    """
    if not user or not user.is_authenticated:
        return {"exo_pct": 100.0, "total_tracked": 0, "mastered": 0, "recovering": 0, "struggling": 0}

    cards = list(MemoryCard.objects.filter(user=user, topic__subject=subject))
    total = len(cards)
    if total == 0:
        return {"exo_pct": 100.0, "total_tracked": 0, "mastered": 0, "recovering": 0, "struggling": 0}

    statuses = [compute_recovery_status(c) for c in cards]
    mastered = statuses.count("mastered")
    recovering = statuses.count("recovering")
    struggling = statuses.count("struggling")

    exo_pct = round(100.0 * (mastered + recovering) / total, 1)

    return {
        "exo_pct": exo_pct,
        "total_tracked": total,
        "mastered": mastered,
        "recovering": recovering,
        "struggling": struggling,
    }


def track_exam_item_outcome(
    user,
    subject: str,
    item_uid: str,
    topic_id: str,
    correct: bool,
    response_time_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Ingestion ML pour chaque item corrigé lors d'un examen blanc officiel.
    """
    item, _ = ItemParameters.objects.get_or_create(
        item_uid=f"exam:{item_uid}",
        defaults={"source_type": "examen_blanc"},
    )
    topic, _ = TopicNode.objects.get_or_create(
        topic_id=topic_id,
        defaults={"label": topic_id, "subject": subject},
    )
    return record_exercise_attempt(
        user,
        subject,
        item,
        topic,
        correct,
        is_examen_blanc=True,
        response_time_seconds=response_time_seconds,
    )
