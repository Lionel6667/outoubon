"""
Diagnostic causal : remonte récursivement le graphe de prérequis
pour identifier la véritable cause racine derrière une lacune observée.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set
from core.models import TopicMastery, TopicNode


def diagnose_root_cause(
    user,
    weak_topic: TopicNode,
    mastery_threshold: float = 0.55,
    max_depth: int = 4,
    visited: Optional[Set[int]] = None,
) -> List[TopicNode]:
    """
    Retourne la liste des topics prérequis qui sont eux-mêmes fragiles,
    en remontant récursivement le graphe.
    Le premier élément de la liste retournée est la cause racine la plus profonde.
    """
    if not weak_topic or not weak_topic.pk:
        return []

    visited = visited if visited is not None else set()
    if weak_topic.pk in visited or max_depth <= 0:
        return []
    visited.add(weak_topic.pk)

    root_causes: List[TopicNode] = []
    prereqs = list(weak_topic.prerequisites.all())

    for prereq in prereqs:
        mastery = None
        if user and user.is_authenticated:
            mastery = TopicMastery.objects.filter(user=user, topic=prereq).first()
        p_mastery = mastery.p_mastery if mastery else 0.15

        if p_mastery < mastery_threshold:
            # Récursion : creuser si le prérequis a lui-même des sous-prérequis non maîtrisés
            deeper = diagnose_root_cause(user, prereq, mastery_threshold, max_depth - 1, visited)
            root_causes.extend(deeper)
            root_causes.append(prereq)

    return root_causes


def get_recommended_starting_point(user, weak_topic: TopicNode) -> TopicNode:
    """
    Retourne le topic par lequel l'élève doit commencer pour débloquer sa progression :
    soit la cause racine la plus profonde si identifiée, soit le topic faible lui-même.
    """
    causes = diagnose_root_cause(user, weak_topic)
    return causes[0] if causes else weak_topic


def build_action_card(user, weak_topic: TopicNode) -> Dict[str, any]:
    """
    Génère une carte d'action explicable et transparente pour le Dashboard ou la page Progression.
    """
    root = get_recommended_starting_point(user, weak_topic)
    is_root_cause = root.pk != weak_topic.pk

    symptom_label = weak_topic.label or weak_topic.topic_id
    root_label = root.label or root.topic_id
    subj = weak_topic.subject or "maths"

    if is_root_cause:
        reason = (
            f"Ta difficulté en « {symptom_label} » provient probablement de « {root_label} », "
            "un concept prérequis fondamental non encore consolidé."
        )
    else:
        reason = f"Le chapitre « {symptom_label} » nécessite une consolidation directe."

    return {
        "id": f"causal_{root.topic_id}",
        "type": "causal_diagnostic",
        "subject": subj,
        "symptom_topic": symptom_label,
        "recommended_topic": root_label,
        "topic_id": root.topic_id,
        "is_root_cause": is_root_cause,
        "title": f"Priorité Fondamentale : {root_label}" if is_root_cause else f"Focus : {root_label}",
        "description": reason,
        "action_label": f"Réviser {root_label}",
        "action_url": f"/dashboard/cours/?subject={subj}&topic={root.topic_id}",
        "badge": "Cause Racine" if is_root_cause else "Lacune",
        "badge_color": "#ef4444" if is_root_cause else "#f59e0b",
        "action_links": {
            "cours": f"/dashboard/cours/?subject={subj}&topic={root.topic_id}",
            "quiz": f"/dashboard/quiz/?subject={subj}&topic={root.topic_id}",
            "exercice": f"/dashboard/exercices/?subject={subj}&topic={root.topic_id}",
        },
    }
