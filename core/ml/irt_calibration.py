"""
Recalibration batch des paramètres d'items IRT 2PL (difficulty_b, discrimination_a)
à partir de l'historique complet des réponses des élèves.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple
from django.utils import timezone
from core.models import ItemParameters, QuizSession, StudentAbility


def calibrate_items_for_subject(
    subject: str,
    response_matrix,
    item_uids: List[str],
    student_thetas,
) -> Dict[str, Tuple[float, float, int]]:
    """
    response_matrix : matrice 2D (n_students, n_items) avec valeurs {1, 0, None/nan}
    item_uids       : liste des item_uids
    student_thetas  : liste/array des thetas des élèves
    """
    n_items = len(item_uids)
    results = {}

    try:
        import numpy as np
        from scipy.optimize import minimize
        has_scipy = True
    except ImportError:
        has_scipy = False

    if has_scipy:
        for j in range(n_items):
            col = response_matrix[:, j]
            mask = ~np.isnan(col)
            count = int(mask.sum())
            if count < 10:
                continue

            y = col[mask]
            theta = student_thetas[mask]

            def neg_log_likelihood(params):
                a, b = params
                a = max(0.2, a)
                z = np.clip(a * (theta - b), -30, 30)
                p = 1.0 / (1.0 + np.exp(-z))
                eps = 1e-6
                p = np.clip(p, eps, 1.0 - eps)
                return -float(np.sum(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))

            res = minimize(neg_log_likelihood, x0=[1.0, 0.0], method="Nelder-Mead")
            a_est, b_est = res.x
            a_est = max(0.3, min(3.0, float(a_est)))
            b_est = max(-4.0, min(4.0, float(b_est)))
            results[item_uids[j]] = (a_est, b_est, count)
    else:
        # Fallback pure Python avec estimation empirique
        for j in range(n_items):
            obs = []
            for i, row in enumerate(response_matrix):
                val = row[j]
                if val is not None and not math.isnan(val):
                    obs.append((float(student_thetas[i]), float(val)))
            if len(obs) < 10:
                continue
            correct_cnt = sum(y for _, y in obs)
            pct = correct_cnt / len(obs)
            # Logit de difficulté approximée
            pct_clamped = max(0.05, min(0.95, pct))
            b_est = -math.log(pct_clamped / (1.0 - pct_clamped))
            a_est = 1.0
            results[item_uids[j]] = (a_est, b_est, len(obs))

    return results


def run_calibration_job(subject: str = "") -> int:
    """
    Parcourt l'historique des QuizSession et recalibre les items en base.
    """
    sessions = QuizSession.objects.all().order_by("-completed_at")[:2000]
    if subject:
        sessions = sessions.filter(subject=subject)

    user_sessions: Dict[int, list] = {}
    item_responses: Dict[str, list] = {}

    for s in sessions:
        details = s.details or []
        for d in details:
            qid = d.get("question_id") or d.get("id")
            if not qid:
                continue
            item_uid = f"quiz:{qid}"
            ok = 1.0 if d.get("ok") or d.get("correct") else 0.0
            if item_uid not in item_responses:
                item_responses[item_uid] = []
            item_responses[item_uid].append((s.user_id, ok))

    updated_count = 0
    now = timezone.now()

    for item_uid, responses in item_responses.items():
        if len(responses) < 5:
            continue
        total = len(responses)
        corrects = sum(r[1] for r in responses)
        pct = corrects / total
        pct_clamped = max(0.05, min(0.95, pct))
        # Estimation logit b
        b_est = max(-3.5, min(3.5, -math.log(pct_clamped / (1.0 - pct_clamped))))
        a_est = 1.0

        item, _ = ItemParameters.objects.get_or_create(
            item_uid=item_uid,
            defaults={"source_type": "quiz", "difficulty_b": b_est, "discrimination_a": a_est},
        )
        item.difficulty_b = b_est
        item.discrimination_a = a_est
        item.n_responses = total
        item.last_calibrated = now
        item.save(update_fields=["difficulty_b", "discrimination_a", "n_responses", "last_calibrated"])
        updated_count += 1

    return updated_count
