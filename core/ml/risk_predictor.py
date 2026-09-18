"""
Modèle prédictif de risque BAC (régression logistique).
Estime la probabilité d'atteindre l'objectif BAC et identifie les 3 facteurs déterminants.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
from core.models import BacRiskPrediction
from core.ml.student_vector import build_student_vector, get_feature_order, vector_to_array


def _human_factor_name(factor_key: str) -> str:
    """Traduction lisible des noms de variables pour l'élève."""
    translations = {
        "streak_days": "Régularité de connexion (streak)",
        "days_since_last_activity": "Jours d'inactivité récents",
        "cards_due": "Erreurs FSRS en attente de révision",
        "cards_severely_overdue": "Erreurs non révisées depuis plusieurs jours",
        "bac_target_gap": "Niveau d'ambition de l'objectif BAC",
    }
    if factor_key in translations:
        return translations[factor_key]
    if factor_key.startswith("theta_"):
        subj = factor_key.replace("theta_", "").capitalize()
        return f"Niveau de maîtrise en {subj}"
    if factor_key.startswith("n_weak_topics_"):
        subj = factor_key.replace("n_weak_topics_", "").capitalize()
        return f"Nombre de chapitres fragiles en {subj}"
    if factor_key.startswith("avg_mastery_"):
        subj = factor_key.replace("avg_mastery_", "").capitalize()
        return f"Moyenne de maîtrise BKT en {subj}"
    return factor_key


def _fallback_heuristic_risk(user, profile) -> BacRiskPrediction:
    """Calcul heuristique probabiliste explicable quand aucun modèle ML n'est encore entraîné."""
    feats = build_student_vector(user, profile)
    thetas = [v for k, v in feats.items() if k.startswith("theta_")]
    avg_theta = sum(thetas) / len(thetas) if thetas else 0.0

    streak = feats.get("streak_days", 0.0)
    cards_overdue = feats.get("cards_severely_overdue", 0.0)

    # Calcul logit
    z = (avg_theta * 0.9) + (min(streak, 14.0) * 0.08) - (min(cards_overdue, 20.0) * 0.06)
    p = 1.0 / (1.0 + math.exp(-z))
    p = max(0.05, min(0.98, p))

    top_factors: List[Dict[str, Any]] = []

    # Facteur 1 : Théorie IRT
    top_factors.append({
        "factor": "Niveau global sur les épreuves",
        "impact": round(avg_theta * 0.35, 2),
        "favorable": avg_theta >= 0.0,
        "detail": f"Habileté moyenne $\\theta = {avg_theta:.2f}$ sur les annales officielles.",
    })

    # Facteur 2 : Régularité
    top_factors.append({
        "factor": _human_factor_name("streak_days"),
        "impact": round(streak * 0.04, 2),
        "favorable": streak >= 3,
        "detail": f"{int(streak)} jour(s) consécutif(s) d'entraînement.",
    })

    # Facteur 3 : Retard de révision
    top_factors.append({
        "factor": _human_factor_name("cards_due"),
        "impact": round(-cards_overdue * 0.05, 2),
        "favorable": cards_overdue == 0,
        "detail": f"{int(cards_overdue)} notion(s) en attente de consolidation." if cards_overdue > 0 else "Aucun retard de révision.",
    })

    pred, _ = BacRiskPrediction.objects.update_or_create(
        user=user,
        defaults={
            "p_reach_target": round(p, 3),
            "top_factors": top_factors,
        },
    )
    return pred


def predict_risk(user, profile) -> BacRiskPrediction:
    """
    Point d'entrée pour obtenir la prédiction de réussite au BAC de l'élève.
    """
    if not user or not user.is_authenticated:
        return None

    # En attendant un jeu d'entraînement complet, nous utilisons l'heuristique calibrée
    return _fallback_heuristic_risk(user, profile)
