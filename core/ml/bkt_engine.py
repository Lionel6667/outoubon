"""
Bayesian Knowledge Tracing (BKT).
Modélise la probabilité de maîtrise latente P(L) pour chaque compétence / topic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
from core.models import TopicMastery, TopicNode


@dataclass
class BKTParams:
    p_init: float = 0.15      # probabilité de maîtrise a priori
    p_transit: float = 0.20   # probabilité d'apprentissage à chaque tentative
    p_slip: float = 0.10      # probabilité d'étourderie (faux négatif)
    p_guess: float = 0.20     # probabilité de deviner juste (faux positif)


DEFAULT_PARAMS_BY_SOURCE: Dict[str, BKTParams] = {
    "qcm_4_choix": BKTParams(p_guess=0.25, p_slip=0.10),
    "question_ouverte": BKTParams(p_guess=0.05, p_slip=0.08),
    "exercice_calcul": BKTParams(p_guess=0.08, p_slip=0.15),
    "flashcard": BKTParams(p_guess=0.10, p_slip=0.12),
}


def bkt_update(p_mastery_prior: float, correct: bool, params: Optional[BKTParams] = None) -> float:
    """
    Une itération de mise à jour bayésienne BKT.
    Retourne la nouvelle probabilité P(maîtrise) ∈ [0.01, 0.99].
    """
    if params is None:
        params = BKTParams()

    if correct:
        numerator = p_mastery_prior * (1.0 - params.p_slip)
        denominator = numerator + (1.0 - p_mastery_prior) * params.p_guess
    else:
        numerator = p_mastery_prior * params.p_slip
        denominator = numerator + (1.0 - p_mastery_prior) * (1.0 - params.p_guess)

    p_mastery_given_obs = numerator / denominator if denominator > 0 else p_mastery_prior

    # Transition d'apprentissage : l'élève peut apprendre même lors d'une tentative ratée
    p_mastery_posterior = p_mastery_given_obs + (1.0 - p_mastery_given_obs) * params.p_transit

    return min(0.99, max(0.01, p_mastery_posterior))


def record_response_and_update_mastery(
    user,
    topic: TopicNode,
    correct: bool,
    source_format: str = "qcm_4_choix",
) -> Optional[TopicMastery]:
    """
    Point d'entrée appelé lors de chaque réponse (quiz, exercice, examen).
    """
    if not user or not user.is_authenticated or not topic:
        return None

    params = DEFAULT_PARAMS_BY_SOURCE.get(source_format, BKTParams())

    mastery, created = TopicMastery.objects.get_or_create(
        user=user,
        topic=topic,
        defaults={"p_mastery": params.p_init, "n_observations": 0},
    )

    prior = mastery.p_mastery if not created else params.p_init
    mastery.p_mastery = bkt_update(prior, correct, params)
    mastery.n_observations += 1
    mastery.save(update_fields=["p_mastery", "n_observations", "last_updated"])

    return mastery


def get_weak_topics(user, subject: str = "", threshold: float = 0.55, limit: int = 10) -> List[TopicMastery]:
    """
    Retourne les concepts dont la probabilité de maîtrise est < seuil,
    triés du plus urgent au moins urgent (au moins 2 observations).
    """
    if not user or not user.is_authenticated:
        return []

    qs = TopicMastery.objects.filter(
        user=user,
        p_mastery__lt=threshold,
        n_observations__gte=2,
    )
    if subject:
        qs = qs.filter(topic__subject=subject)

    return list(qs.select_related("topic").order_by("p_mastery")[:limit])


def get_mastered_topics(user, subject: str = "", threshold: float = 0.85, limit: int = 20) -> List[TopicMastery]:
    """
    Retourne les concepts maîtrisés (P >= seuil).
    """
    if not user or not user.is_authenticated:
        return []

    qs = TopicMastery.objects.filter(
        user=user,
        p_mastery__gte=threshold,
    )
    if subject:
        qs = qs.filter(topic__subject=subject)

    return list(qs.select_related("topic").order_by("-p_mastery")[:limit])
