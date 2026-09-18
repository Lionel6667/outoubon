"""
Moteur IRT 2PL : calibration des items (batch) + mise à jour temps réel
de l'habileté (theta) de l'élève après chaque réponse.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Set
from django.db import transaction
from core.models import ItemParameters, StudentAbility


def probability_correct(theta: float, a: float, b: float) -> float:
    """P(succès | theta) sous le modèle 2PL logistique."""
    z = a * (theta - b)
    # clamp pour éviter overflow numérique
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def update_student_theta(
    user,
    subject: str,
    item: ItemParameters,
    correct: bool,
    learning_rate: Optional[float] = None,
) -> StudentAbility:
    """
    Met à jour theta de l'élève après UNE réponse, via une descente de
    gradient sur la log-vraisemblance (online IRT / Elo étendu).

    theta_new = theta_old + K * a * (observed - expected)
    """
    if not user or not user.is_authenticated:
        return None

    ability, _ = StudentAbility.objects.get_or_create(
        user=user,
        subject=subject,
        defaults={"theta": 0.0, "theta_se": 1.0, "n_responses": 0},
    )

    expected = probability_correct(ability.theta, item.discrimination_a, item.difficulty_b)
    observed = 1.0 if correct else 0.0

    # K décroissant : réactif au début (nouvel élève), stable ensuite
    if learning_rate is None:
        n = ability.n_responses
        learning_rate = max(0.05, 0.5 / (1.0 + n / 10.0))

    delta = learning_rate * item.discrimination_a * (observed - expected)
    ability.theta = max(-4.0, min(4.0, ability.theta + delta))

    # L'erreur-type diminue avec le nombre de réponses (plus de confiance statistique)
    ability.n_responses += 1
    ability.theta_se = max(0.15, 1.0 / math.sqrt(1.0 + ability.n_responses * 0.3))

    ability.save(update_fields=["theta", "theta_se", "n_responses", "updated_at"])
    return ability


def theta_to_display_score(theta: float) -> float:
    """
    Convertit theta (échelle ~[-4, 4]) en score 0-100% pour l'affichage
    sur le dashboard et la page progression.
    """
    score = 100.0 / (1.0 + math.exp(-1.1 * theta))
    return round(score, 1)


def select_next_adaptive_item(
    user,
    subject: str,
    candidate_items: Iterable[ItemParameters],
    exclude_uids: Optional[Set[str]] = None,
) -> Optional[ItemParameters]:
    """
    Choisit l'item maximisant l'information de Fisher au niveau d'habileté courant de l'élève.
    """
    exclude_uids = exclude_uids or set()
    ability = None
    if user and user.is_authenticated:
        ability = StudentAbility.objects.filter(user=user, subject=subject).first()
    theta = ability.theta if ability else 0.0

    best_item: Optional[ItemParameters] = None
    best_score = -1.0

    for item in candidate_items:
        if item.item_uid in exclude_uids:
            continue
        p = probability_correct(theta, item.discrimination_a, item.difficulty_b)
        info = (item.discrimination_a ** 2) * p * (1.0 - p)
        if info > best_score:
            best_score = info
            best_item = item

    return best_item
