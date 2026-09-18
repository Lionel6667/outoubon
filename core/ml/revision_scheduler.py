"""
Planificateur de révision intelligent (Sac-à-dos / Knapsack + Priorité Composite).
Optimise l'utilisation du temps réel de l'élève en évitant la surcharge cognitive.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from core.ml.bkt_engine import get_weak_topics
from core.ml.fsrs_engine import get_due_cards
from core.ml.prerequisite_graph import build_action_card

COGNITIVE_LOAD_BY_SUBJECT: Dict[str, float] = {
    "maths": 0.90,
    "physique": 0.85,
    "chimie": 0.80,
    "svt": 0.60,
    "economie": 0.65,
    "francais": 0.40,
    "philosophie": 0.55,
    "histoire_geo": 0.45,
    "anglais": 0.35,
    "litterature": 0.40,
}

TIME_COST_MINUTES: Dict[str, int] = {
    "memory_card": 3,       # Flashcard / Révision FSRS express
    "weak_topic_quiz": 10,  # Quiz ciblé sur une lacune BKT
    "cours_chapitre": 20,   # Lecture / Exercice de consolidation de cours
}


@dataclass
class RevisionItem:
    kind: str            # "memory_card", "weak_topic_quiz", "cours_chapitre"
    subject: str
    label: str
    priority: float
    time_cost: int        # minutes
    action_url: str
    badge: str = ""
    description: str = ""


def compute_priority(
    subject: str,
    urgency: float,
    gap_to_target: float,
    coefficient: float,
    weights: Tuple[float, float, float, float] = (0.35, 0.25, 0.25, 0.15),
) -> float:
    """
    Priorité Composite = w1*Urgence + w2*Écart + w3*Coefficient - w4*ChargeCognitive
    """
    w1, w2, w3, w4 = weights
    cognitive_load = COGNITIVE_LOAD_BY_SUBJECT.get(subject, 0.50)
    p = (w1 * urgency) + (w2 * gap_to_target) + (w3 * coefficient) - (w4 * cognitive_load)
    return round(p, 4)


def build_candidate_pool(
    user,
    profile,
    subject_coefficients: Dict[str, float],
    subject_gaps: Dict[str, float],
) -> List[RevisionItem]:
    """
    Construit l'ensemble des activités candidates prioritaires pour l'élève.
    """
    pool: List[RevisionItem] = []

    # 1. Cartes FSRS en retard de révision (Urgence maximale = 1.0)
    due_cards = get_due_cards(user, limit=50)
    for card in due_cards:
        subj = card.topic.subject or "maths"
        coef = subject_coefficients.get(subj, 0.5)
        gap = subject_gaps.get(subj, 0.5)
        p = compute_priority(subj, 1.0, gap, coef)

        pool.append(RevisionItem(
            kind="memory_card",
            subject=subj,
            label=f"Erreur FSRS : {card.topic.label}",
            priority=p,
            time_cost=TIME_COST_MINUTES["memory_card"],
            action_url=f"/dashboard/quiz/?subject={subj}&review=1",
            badge="FSRS",
            description=f"Consolidation de ta carte mémoire en {subj.upper()}",
        ))

    # 2. Lacunes de concepts identifiées par BKT
    for subj, coef in subject_coefficients.items():
        weak_topics = get_weak_topics(user, subj, threshold=0.55, limit=3)
        gap = subject_gaps.get(subj, 0.5)
        for tm in weak_topics:
            urgency = max(0.1, 1.0 - tm.p_mastery)
            p = compute_priority(subj, urgency, gap, coef)
            card = build_action_card(user, tm.topic)

            pool.append(RevisionItem(
                kind="weak_topic_quiz",
                subject=subj,
                label=f"Quiz ciblé : {card['recommended_topic']}",
                priority=p,
                time_cost=TIME_COST_MINUTES["weak_topic_quiz"],
                action_url=card["action_links"]["quiz"],
                badge="Lacune",
                description=card["reason"],
            ))

    # Tri par priorité décroissante
    pool.sort(key=lambda item: item.priority, reverse=True)
    return pool


def build_session_plan(
    user,
    profile,
    available_minutes: int,
    subject_coefficients: Dict[str, float],
    subject_gaps: Dict[str, float],
) -> List[RevisionItem]:
    """
    Algorithme du sac-à-dos glouton pour remplir le temps disponible de l'élève.
    Évite d'enchaîner plus de 30 minutes de matières à haute charge cognitive.
    """
    pool = build_candidate_pool(user, profile, subject_coefficients, subject_gaps)
    plan: List[RevisionItem] = []
    remaining = max(10, available_minutes)

    last_heavy_subj: Optional[str] = None
    consecutive_heavy_mins = 0

    for item in pool:
        if item.time_cost > remaining:
            continue

        is_heavy = COGNITIVE_LOAD_BY_SUBJECT.get(item.subject, 0.5) >= 0.80
        if is_heavy and item.subject == last_heavy_subj and consecutive_heavy_mins >= 30:
            continue

        plan.append(item)
        remaining -= item.time_cost

        if is_heavy:
            if item.subject == last_heavy_subj:
                consecutive_heavy_mins += item.time_cost
            else:
                consecutive_heavy_mins = item.time_cost
            last_heavy_subj = item.subject
        else:
            consecutive_heavy_mins = 0
            last_heavy_subj = None

        if remaining <= 0:
            break

    return plan


def build_weekly_plan(
    user,
    profile,
    minutes_per_day: Dict[str, int],
    subject_coefficients: Dict[str, float],
    subject_gaps: Dict[str, float],
) -> Dict[str, List[RevisionItem]]:
    """
    Construit le planning de la semaine jour par jour.
    """
    return {
        day: build_session_plan(user, profile, minutes, subject_coefficients, subject_gaps)
        for day, minutes in minutes_per_day.items()
    }
