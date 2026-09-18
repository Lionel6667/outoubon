"""
LE MOTEUR DE COMPILATION DE LA NOTE BAC.
Combine IRT + BKT + FSRS (décroissance par oubli) + trajectoire/vélocité
pour produire la note BAC estimée, sa fourchette de confiance Monte Carlo,
et sa projection jusqu'au jour de l'examen.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from django.db.models import Avg
from core.models import MemoryCard, StudentAbility, ThetaHistory
from core.series_data import SERIES
from core.ml.irt_engine import theta_to_display_score
from core.ml.fsrs_engine import retrievability

BAC_TOTAL_POINTS = 1900

MENTION_THRESHOLDS = {
    "admission": 0.50,    # 950/1900 (10/20)
    "assez_bien": 0.60,   # 1140/1900 (12/20)
    "bien": 0.70,         # 1330/1900 (14/20)
    "tres_bien": 0.80,    # 1520/1900 (16/20)
}


def effective_theta_with_decay(user, subject: str) -> Tuple[float, float, bool]:
    """
    Applique la décroissance par oubli : theta brut (IRT) x facteur de
    rétrievabilité moyenne des MemoryCard liées à cette matière.

    Formule : effective_theta = theta * (0.5 + 0.5 * avg_retrievability)
    -> une matière totalement fraîche (R moyen = 1) garde 100% de theta.
    -> une matière oubliée (R moyen = 0) perd jusqu'à 50% de theta affiché.

    Retourne (effective_theta, avg_retrievability, is_stale)
    """
    if not user or not user.is_authenticated:
        return 0.0, 1.0, False

    ability = StudentAbility.objects.filter(user=user, subject=subject).first()
    if not ability or ability.n_responses < 3:
        return (ability.theta if ability else 0.0), 1.0, False

    cards = MemoryCard.objects.filter(user=user, topic__subject=subject)
    if not cards.exists():
        return ability.theta, 1.0, False

    now = date.today()
    retrievs: List[float] = []
    for card in cards:
        if card.last_review:
            elapsed = (now - card.last_review.date()).days
            retrievs.append(retrievability(elapsed, card.stability))
        else:
            retrievs.append(1.0)

    avg_r = float(sum(retrievs) / len(retrievs)) if retrievs else 1.0
    effective_theta = ability.theta * (0.5 + 0.5 * avg_r)
    is_stale = avg_r < 0.6

    return effective_theta, avg_r, is_stale


def compute_subject_score(user, subject: str, diag_fallback_score: Optional[int] = None) -> Dict[str, Any]:
    """
    Score par matière avec décroissance appliquée.
    Fallback = 0 conservé à l'identique du système officiel Outoubon.
    """
    if not user or not user.is_authenticated:
        return {"score": diag_fallback_score or 0, "source": "guest", "retrievability": 1.0, "is_stale": False}

    ability = StudentAbility.objects.filter(user=user, subject=subject).first()

    if ability and ability.n_responses >= 5:
        eff_theta, avg_r, is_stale = effective_theta_with_decay(user, subject)
        score = theta_to_display_score(eff_theta)
        return {
            "score": score,
            "source": "irt_decayed",
            "retrievability": round(avg_r, 3),
            "is_stale": is_stale,
        }

    if diag_fallback_score is not None:
        return {
            "score": int(diag_fallback_score),
            "source": "diagnostic",
            "retrievability": 1.0,
            "is_stale": False,
        }

    return {
        "score": 0,
        "source": "no_data",
        "retrievability": 1.0,
        "is_stale": False,
    }


def compute_bac_point_estimate(user, serie: str, diag_scores: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """
    Compilation exacte de la note /1900 avec décroissance par oubli intégrée.
    """
    diag_scores = diag_scores or {}
    serie_cfg = SERIES.get(serie, SERIES["SVT"])
    coeffs = serie_cfg.get("subjects", {})
    total_coeff = sum(coeffs.values())

    breakdown = {}
    weighted_sum = 0.0
    for subject, coef in coeffs.items():
        result = compute_subject_score(user, subject, diag_scores.get(subject))
        breakdown[subject] = {**result, "coefficient": coef}
        weighted_sum += (result["score"] / 100.0) * coef

    bac_score = round((weighted_sum / total_coeff) * BAC_TOTAL_POINTS) if total_coeff > 0 else 0

    return {
        "bac_score": bac_score,
        "breakdown": breakdown,
        "total_coeff": total_coeff,
    }


def compute_velocity(user, subject: str, window_days: int = 21) -> float:
    """
    Pente de progression en points de score (0-100) par jour, calculée
    par régression linéaire sur les N derniers jours de ThetaHistory.
    """
    if not user or not user.is_authenticated:
        return 0.0

    since = date.today() - timedelta(days=window_days)
    history = list(
        ThetaHistory.objects.filter(user=user, subject=subject, date__gte=since)
        .order_by("date")
        .values_list("date", "display_score")
    )
    if len(history) < 3:
        return 0.0

    dates, scores = zip(*history)
    x = [(d - dates[0]).days for d in dates]
    y = list(scores)
    n = len(x)

    # Calcul des moindres carrés en pur Python
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    denom = sum((xi - mean_x) ** 2 for xi in x)
    if denom == 0:
        return 0.0
    slope = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / denom
    return float(slope)


def compute_required_velocity(bac_gap_target: int, days_remaining: int) -> float:
    """Vélocité requise en POINTS BAC par jour pour atteindre bac_target."""
    if days_remaining <= 0:
        return float("inf") if bac_gap_target > 0 else 0.0
    return round(bac_gap_target / days_remaining, 2)


def get_velocity_status(actual_velocity_bac_points_per_day: float, required_velocity: Optional[float]) -> str:
    """Statut de vélocité explicable pour l'élève."""
    if required_velocity is None or required_velocity <= 0:
        return "objectif_atteint"
    ratio = actual_velocity_bac_points_per_day / required_velocity if required_velocity > 0 else 0
    if ratio >= 1.1:
        return "en_avance"
    if ratio >= 0.85:
        return "sur_la_bonne_voie"
    if ratio >= 0.4:
        return "en_retard"
    return "tres_en_retard"


def compute_confidence_multiplier(streak_days: int) -> float:
    """Le streak resserre ou élargit l'incertitude de la projection."""
    if streak_days >= 21:
        return 0.75
    if streak_days >= 7:
        return 1.0
    if streak_days >= 2:
        return 1.25
    return 1.6


def simulate_bac_score_distribution(
    user,
    profile,
    serie: str,
    diag_scores: Optional[Dict[str, int]] = None,
    exam_date: Optional[date] = None,
    n_simulations: int = 1500,
) -> Dict[str, Any]:
    """
    Simulation Monte Carlo : projette n trajectoires jusqu'au jour du BAC.
    Retourne P10, P50 (médiane), P90, probabilité par mention et probabilité d'atteinte de la cible.
    """
    import random

    exam_date = exam_date or date(date.today().year if date.today().month < 7 else date.today().year + 1, 6, 15)
    days_remaining = max(1, (exam_date - date.today()).days)

    serie_cfg = SERIES.get(serie, SERIES["SVT"])
    coeffs = serie_cfg.get("subjects", {})
    total_coeff = sum(coeffs.values())
    confidence_mult = compute_confidence_multiplier(getattr(profile, "streak", 0) or 0)

    subject_params = {}
    for subject in coeffs:
        ability = StudentAbility.objects.filter(user=user, subject=subject).first() if user and user.is_authenticated else None
        eff_theta, _, _ = effective_theta_with_decay(user, subject)
        velocity = compute_velocity(user, subject)
        theta_velocity = velocity / 27.5 if velocity else 0.0

        theta_now = eff_theta if ability else 0.0
        se = (ability.theta_se if ability else 1.5) * confidence_mult

        subject_params[subject] = {
            "theta_now": theta_now,
            "theta_velocity": theta_velocity,
            "se": se,
            "has_data": bool(ability and ability.n_responses >= 5),
            "diag_fallback": diag_scores.get(subject) if diag_scores else None,
        }

    totals = []
    for _ in range(n_simulations):
        weighted_sum = 0.0
        for subject, coef in coeffs.items():
            p = subject_params[subject]
            if p["has_data"]:
                projected_theta = p["theta_now"] + p["theta_velocity"] * days_remaining
                noise = random.gauss(0, p["se"])
                score = theta_to_display_score(projected_theta + noise)
            elif p["diag_fallback"] is not None:
                score = p["diag_fallback"]
            else:
                score = 0
            score_clamped = max(0.0, min(100.0, score))
            weighted_sum += (score_clamped / 100.0) * coef

        tot = round((weighted_sum / total_coeff) * BAC_TOTAL_POINTS) if total_coeff > 0 else 0
        totals.append(tot)

    totals.sort()
    n = len(totals)
    p10 = totals[int(0.10 * n)]
    p50 = totals[int(0.50 * n)]
    p90 = totals[int(0.90 * n)]
    mean_val = round(sum(totals) / n, 1)

    prob_mentions = {
        name: round(sum(1 for t in totals if t >= threshold * BAC_TOTAL_POINTS) / n, 3)
        for name, threshold in MENTION_THRESHOLDS.items()
    }

    bac_target = getattr(profile, "bac_target", None)
    prob_target = round(sum(1 for t in totals if t >= bac_target) / n, 3) if bac_target else None

    return {
        "days_remaining": days_remaining,
        "p10": p10,
        "p50_median": p50,
        "p90": p90,
        "mean": mean_val,
        "probability_by_mention": prob_mentions,
        "probability_reach_target": prob_target,
    }


def simulate_intervention(
    user,
    profile,
    serie: str,
    subject: str,
    theta_gain: float,
    diag_scores: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """
    Simulateur de levier pédagogique :
    Calcule précisément le gain en points BAC si theta augmente de +delta_theta.
    """
    before = compute_bac_point_estimate(user, serie, diag_scores)

    ability = StudentAbility.objects.filter(user=user, subject=subject).first() if user and user.is_authenticated else None
    current_theta = ability.theta if ability else 0.0
    simulated_theta = current_theta + theta_gain
    simulated_score = theta_to_display_score(simulated_theta)

    serie_cfg = SERIES.get(serie, SERIES["SVT"])
    coeffs = serie_cfg.get("subjects", {})
    total_coeff = sum(coeffs.values())

    new_weighted_sum = 0.0
    for subj, coef in coeffs.items():
        if subj == subject:
            new_weighted_sum += (simulated_score / 100.0) * coef
        else:
            prev_score = before["breakdown"].get(subj, {}).get("score", 0)
            new_weighted_sum += (prev_score / 100.0) * coef

    after_score = round((new_weighted_sum / total_coeff) * BAC_TOTAL_POINTS) if total_coeff > 0 else 0

    return {
        "subject": subject,
        "before_bac_score": before["bac_score"],
        "after_bac_score": after_score,
        "points_gained": max(0, after_score - before["bac_score"]),
        "theta_gain_required": theta_gain,
    }


def compile_full_bac_report(
    user,
    profile,
    serie: str,
    diag_scores: Optional[Dict[str, int]] = None,
    exam_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Point d'entrée unique complet pour le Dashboard et la page Progression.
    """
    point_estimate = compute_bac_point_estimate(user, serie, diag_scores)
    distribution = simulate_bac_score_distribution(user, profile, serie, diag_scores, exam_date)

    bac_score = point_estimate["bac_score"]
    bac_target = getattr(profile, "bac_target", None)
    bac_gap_target = max(0, bac_target - bac_score) if bac_target else None
    bac_gap_pass = max(0, 950 - bac_score)

    required_velocity = (
        compute_required_velocity(bac_gap_target, distribution["days_remaining"])
        if bac_gap_target is not None else None
    )

    serie_cfg = SERIES.get(serie, SERIES["SVT"])
    coeffs = serie_cfg.get("subjects", {})
    total_coeff = sum(coeffs.values())

    actual_velocity_score = (
        sum(compute_velocity(user, s) * c for s, c in coeffs.items()) / total_coeff
        if total_coeff else 0.0
    )
    actual_velocity_bac_points = (actual_velocity_score / 100.0) * BAC_TOTAL_POINTS

    velocity_status = (
        get_velocity_status(actual_velocity_bac_points, required_velocity)
        if required_velocity is not None else "pas_d_objectif_defini"
    )

    return {
        "bac_score_now": bac_score,
        "breakdown_by_subject": point_estimate["breakdown"],
        "bac_gap_pass": bac_gap_pass,
        "bac_gap_target": bac_gap_target,
        "projection": distribution,
        "velocity": {
            "actual_bac_points_per_day": round(actual_velocity_bac_points, 2),
            "required_bac_points_per_day": round(required_velocity, 2) if required_velocity else None,
            "status": velocity_status,
        },
    }
