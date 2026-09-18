"""
Bandit Multi-Bras Contextuel (Thompson Sampling).
Apprend dynamiquement quel type d'activité produit le plus de gain d'apprentissage
pour chaque élève spécifique.
"""
from __future__ import annotations

import random
from typing import Dict, List
from core.models import BanditArmStats

ARMS: List[str] = [
    "quiz_court",
    "exercice_srs",
    "fiche_memo",
    "cours_chapitre",
]


def _sample_beta(alpha: float, beta: float) -> float:
    """Tire un échantillon depuis une loi Beta(alpha, beta)."""
    try:
        import numpy as np
        return float(np.random.beta(alpha, beta))
    except ImportError:
        # Approximation via loi Gamma standard en pur Python
        x = random.gammavariate(alpha, 1.0)
        y = random.gammavariate(beta, 1.0)
        return x / (x + y) if (x + y) > 0 else 0.5


def select_arm_thompson_sampling(user) -> str:
    """
    Sélectionne le meilleur format d'activité par Thompson Sampling.
    Équilibre automatiquement l'exploration et l'exploitation.
    """
    if not user or not user.is_authenticated:
        return random.choice(ARMS)

    samples: Dict[str, float] = {}

    for arm in ARMS:
        stats, _ = BanditArmStats.objects.get_or_create(
            user=user,
            arm_name=arm,
            defaults={"alpha": 1.0, "beta": 1.0},
        )
        samples[arm] = _sample_beta(stats.alpha, stats.beta)

    best_arm = max(samples, key=samples.get)
    return best_arm


def record_arm_outcome(user, arm_name: str, learning_gain: bool) -> None:
    """
    Enregistre le retour d'apprentissage après complétion d'une activité.
    learning_gain = True si une progression BKT ou IRT a été mesurée.
    """
    if not user or not user.is_authenticated or arm_name not in ARMS:
        return

    stats, _ = BanditArmStats.objects.get_or_create(
        user=user,
        arm_name=arm_name,
        defaults={"alpha": 1.0, "beta": 1.0},
    )

    if learning_gain:
        stats.alpha += 1.0
    else:
        stats.beta += 1.0

    stats.save(update_fields=["alpha", "beta"])
