"""
Moteur FSRS (Free Spaced Repetition Scheduler) pour OU TOU BON.
Modélise la courbe de rétention mémoire : R(t) = exp(-t / S)
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import List, Optional
from django.utils import timezone
from core.models import MemoryCard, TopicNode

W = {
    "init_stability_again": 0.4,
    "init_stability_hard": 1.0,
    "init_stability_good": 3.0,
    "init_stability_easy": 6.0,
    "init_difficulty": 5.0,
    "difficulty_decay": 0.1,
    "stability_growth": 1.3,
    "retrievability_target": 0.90,
}

RATING_AGAIN = 1
RATING_HARD = 2
RATING_GOOD = 3
RATING_EASY = 4


def retrievability(elapsed_days: float, stability: float) -> float:
    """R(t) = exp(-t / S)"""
    if stability <= 0:
        return 0.0
    return math.exp(-max(0.0, elapsed_days) / max(0.1, stability))


def next_interval_days(stability: float, target_retention: float = W["retrievability_target"]) -> float:
    """Résout R(t) = target_retention => t = -S * ln(target_retention)"""
    return -max(0.1, stability) * math.log(target_retention)


def update_card_after_review(
    card: MemoryCard,
    rating: int,
    now: Optional[datetime] = None,
) -> MemoryCard:
    """
    Met à jour la stabilité S et la difficulté D d'une carte mémoire après révision.
    rating : 1 (Again / échec), 2 (Hard / difficile), 3 (Good / réussi), 4 (Easy / facile).
    """
    now = now or timezone.now()

    if card.reps == 0 or not card.last_review:
        stability_map = {
            RATING_AGAIN: W["init_stability_again"],
            RATING_HARD: W["init_stability_hard"],
            RATING_GOOD: W["init_stability_good"],
            RATING_EASY: W["init_stability_easy"],
        }
        card.stability = stability_map.get(rating, W["init_stability_good"])
        card.difficulty = W["init_difficulty"]
    else:
        elapsed_days = (now - card.last_review).total_seconds() / 86400.0
        r = retrievability(elapsed_days, card.stability)

        if rating == RATING_AGAIN:
            # Chute de stabilité après échec
            card.stability = max(0.3, card.stability * 0.35 * (1.0 - r * 0.3))
            card.difficulty = min(10.0, card.difficulty + W["difficulty_decay"] * 3.0)
            card.lapses += 1
        else:
            # Renforcement selon l'effet d'espacement (plus R était bas, plus le gain est fort)
            hard_penalty = 0.85 if rating == RATING_HARD else 1.0
            easy_bonus = 1.30 if rating == RATING_EASY else 1.0
            spacing_gain = 1.0 + (1.0 - r)
            growth = W["stability_growth"] * hard_penalty * easy_bonus * spacing_gain
            card.stability = max(0.5, card.stability * growth)
            decay = W["difficulty_decay"] if rating != RATING_HARD else 0.0
            card.difficulty = max(1.0, card.difficulty - decay)

    card.retrievability = 1.0
    card.reps += 1
    card.last_review = now

    interval = next_interval_days(card.stability)
    # Entre 1 jour et 180 jours max
    interval = max(1.0, min(180.0, interval))
    card.next_review = now + timedelta(days=interval)

    card.save(update_fields=[
        "stability", "difficulty", "retrievability", "reps",
        "lapses", "last_review", "next_review", "updated_at",
    ])
    return card


def get_due_cards(
    user,
    subject: str = "",
    now: Optional[datetime] = None,
    limit: int = 50,
) -> List[MemoryCard]:
    """
    Retourne les cartes mémoires nécessitant une révision aujourd'hui (next_review <= now).
    """
    if not user or not user.is_authenticated:
        return []

    now = now or timezone.now()
    qs = MemoryCard.objects.filter(user=user, next_review__lte=now)
    if subject:
        qs = qs.filter(topic__subject=subject)

    return list(qs.select_related("topic").order_by("next_review")[:limit])


def create_card_from_mistake(
    user,
    topic: TopicNode,
    item_uid: str = "",
    now: Optional[datetime] = None,
) -> MemoryCard:
    """
    Crée ou réactive une carte mémoire suite à une erreur dans un quiz ou exercice.
    """
    if not user or not user.is_authenticated or not topic:
        return None

    now = now or timezone.now()
    card, created = MemoryCard.objects.get_or_create(
        user=user,
        topic=topic,
        defaults={
            "item_uid": item_uid or "",
            "stability": W["init_stability_again"],
            "difficulty": W["init_difficulty"],
            "retrievability": 1.0,
            "next_review": now + timedelta(days=1),
        },
    )
    if not created:
        update_card_after_review(card, RATING_AGAIN, now)
    return card
