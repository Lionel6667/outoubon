"""
Pipeline d'ingestion unifié pour les moteurs ML (IRT, BKT, FSRS).
Enregistre chaque réponse d'élève en arrière-plan sans bloquer la requête.
"""
from __future__ import annotations

import logging
from typing import Optional
from core.models import ItemParameters, MemoryCard, TopicNode
from core.ml.irt_engine import update_student_theta
from core.ml.bkt_engine import record_response_and_update_mastery
from core.ml.fsrs_engine import (
    create_card_from_mistake,
    update_card_after_review,
    RATING_AGAIN,
    RATING_GOOD,
)

logger = logging.getLogger(__name__)


def _sanitize_slug(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in text.lower().strip())
    return cleaned[:60] or "general"


def track_quiz_answer(
    user,
    subject: str,
    question_id: str,
    enonce: str,
    is_correct: bool,
    theme: str = "",
) -> None:
    """
    Ingestion ML d'une réponse à un quiz.
    Met à jour IRT theta, BKT mastery et FSRS memory card.
    """
    if not user or not user.is_authenticated:
        return

    try:
        theme_clean = (theme or subject or "general").strip()
        topic_slug = f"{subject}.{_sanitize_slug(theme_clean)}"

        topic, _ = TopicNode.objects.get_or_create(
            topic_id=topic_slug,
            defaults={"label": theme_clean, "subject": subject},
        )

        item_uid = f"quiz:{question_id or _sanitize_slug(enonce[:40])}"
        item_params, _ = ItemParameters.objects.get_or_create(
            item_uid=item_uid,
            defaults={"source_type": "quiz", "topic": topic},
        )

        # 1. Mise à jour IRT 2PL
        update_student_theta(user, subject, item_params, is_correct)

        # 2. Mise à jour BKT
        record_response_and_update_mastery(user, topic, is_correct, source_format="qcm_4_choix")

        # 3. Mise à jour FSRS
        if not is_correct:
            create_card_from_mistake(user, topic, item_uid=item_uid)
        else:
            card = MemoryCard.objects.filter(user=user, topic=topic, item_uid=item_uid).first()
            if card:
                update_card_after_review(card, RATING_GOOD)
    except Exception as e:
        logger.warning(f"[ML_TRACKING] Error tracking quiz answer: {e}")


def track_exam_item_outcome(
    user,
    subject: str,
    item_hash: str,
    question_text: str,
    is_correct: bool,
) -> None:
    """
    Ingestion ML d'une réponse d'examen blanc ou d'exercice BAC.
    """
    if not user or not user.is_authenticated:
        return

    try:
        topic_slug = f"{subject}.examen_item"
        topic, _ = TopicNode.objects.get_or_create(
            topic_id=topic_slug,
            defaults={"label": f"Épreuves BAC {subject.upper()}", "subject": subject},
        )

        item_uid = f"exam:{item_hash}"
        item_params, _ = ItemParameters.objects.get_or_create(
            item_uid=item_uid,
            defaults={"source_type": "examen_blanc", "topic": topic},
        )

        # 1. IRT
        update_student_theta(user, subject, item_params, is_correct)

        # 2. BKT
        record_response_and_update_mastery(user, topic, is_correct, source_format="question_ouverte")

        # 3. FSRS
        if not is_correct:
            create_card_from_mistake(user, topic, item_uid=item_uid)
        else:
            card = MemoryCard.objects.filter(user=user, topic=topic, item_uid=item_uid).first()
            if card:
                update_card_after_review(card, RATING_GOOD)
    except Exception as e:
        logger.warning(f"[ML_TRACKING] Error tracking exam item: {e}")
