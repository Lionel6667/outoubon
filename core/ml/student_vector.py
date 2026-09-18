"""
Construit le vecteur numérique unifié représentant un élève.
Sert de socle pour le clustering k-means, le modèle de risque BAC et le bandit.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List
from django.utils import timezone
from core.models import MemoryCard, StudentAbility, TopicMastery
from core.series_data import SERIES

SUBJECTS_BY_SERIE: Dict[str, List[str]] = {
    serie: list(cfg["subjects"].keys())
    for serie, cfg in SERIES.items()
}


def get_feature_order(serie: str) -> List[str]:
    """Retourne la liste ordonnée et stable des noms de variables pour une série."""
    subjects = SUBJECTS_BY_SERIE.get(serie, list(SERIES["SVT"]["subjects"].keys()))
    order = []
    for s in subjects:
        order.extend([
            f"theta_{s}",
            f"confidence_{s}",
            f"avg_mastery_{s}",
            f"n_weak_topics_{s}",
        ])
    order.extend([
        "streak_days",
        "days_since_last_activity",
        "cards_due",
        "cards_severely_overdue",
        "bac_target_gap",
    ])
    return order


def build_student_vector(user, profile) -> Dict[str, float]:
    """
    Construit un dictionnaire explicite {nom_feature: valeur}.
    Garantit 100% d'explicabilité de chaque variable statistique.
    """
    serie = getattr(profile, "serie", "SVT") or "SVT"
    subjects = SUBJECTS_BY_SERIE.get(serie, list(SERIES["SVT"]["subjects"].keys()))
    features: Dict[str, float] = {}

    # 1. Habiletés IRT par matière
    abilities = {
        a.subject: a for a in StudentAbility.objects.filter(user=user)
    }
    for subj in subjects:
        a = abilities.get(subj)
        features[f"theta_{subj}"] = float(a.theta) if a else 0.0
        features[f"confidence_{subj}"] = float(1.0 / a.theta_se) if a and a.theta_se > 0 else 1.0

    # 2. Maîtrise moyenne BKT par matière
    for subj in subjects:
        masteries = TopicMastery.objects.filter(user=user, topic__subject=subj)
        vals = list(masteries.values_list("p_mastery", flat=True))
        features[f"avg_mastery_{subj}"] = float(sum(vals) / len(vals)) if vals else 0.30
        features[f"n_weak_topics_{subj}"] = float(sum(1 for v in vals if v < 0.55))

    # 3. Engagement & Assiduité
    features["streak_days"] = float(getattr(profile, "streak", 0) or 0)
    last_act = getattr(profile, "last_activity", None)
    if last_act:
        today = timezone.localdate()
        features["days_since_last_activity"] = float(max(0, (today - last_act).days))
    else:
        features["days_since_last_activity"] = 7.0

    # 4. Répétition espacée FSRS (charge de révision en attente)
    now = timezone.now()
    due_count = MemoryCard.objects.filter(user=user, next_review__lte=now).count()
    overdue_count = MemoryCard.objects.filter(
        user=user, next_review__lte=now - timedelta(days=3)
    ).count()
    features["cards_due"] = float(due_count)
    features["cards_severely_overdue"] = float(overdue_count)

    # 5. Écart à la cible BAC
    target = getattr(profile, "bac_target", None) or 1300
    features["bac_target_gap"] = float(target) / 1900.0

    return features


def vector_to_array(features: Dict[str, float], feature_order: List[str]):
    """Convertit le dictionnaire en vecteur ordonné (numpy array ou liste de float)."""
    try:
        import numpy as np
        return np.array([features.get(f, 0.0) for f in feature_order], dtype=float)
    except ImportError:
        return [float(features.get(f, 0.0)) for f in feature_order]
