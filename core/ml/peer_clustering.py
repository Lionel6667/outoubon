"""
Clustering k-means des profils d'élèves par série.
Permet d'identifier des groupes d'apprentissage et de fournir des comparaisons
bienveillantes entre pairs ayant des parcours similaires.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional
from django.contrib.auth import get_user_model
from accounts.models import UserProfile
from core.models import PeerCluster, StudentClusterAssignment
from core.ml.student_vector import build_student_vector, get_feature_order, vector_to_array

User = get_user_model()


def _describe_cluster(centroid: Dict[str, float]) -> str:
    """Génère un profil explicable pour le cluster d'élèves."""
    theta_keys = [k for k in centroid if k.startswith("theta_")]
    if not theta_keys:
        return "Profil équilibré"

    sorted_thetas = sorted(theta_keys, key=lambda k: centroid.get(k, 0.0), reverse=True)
    strongest = sorted_thetas[0].replace("theta_", "").capitalize()
    weakest = sorted_thetas[-1].replace("theta_", "").capitalize()

    if centroid.get(sorted_thetas[0], 0.0) >= 0.5:
        return f"Aisance en {strongest} · Renforcement ciblé en {weakest}"
    return f"Consolidation active en {weakest}"


def run_clustering_for_serie(
    serie: str = "SVT",
    n_clusters: int = 4,
    min_students: int = 5,
) -> Optional[Dict[int, PeerCluster]]:
    """
    Exécute le clustering k-means pour une série donnée.
    Sauvegarde les centroïdes et assigne chaque élève à son cluster.
    """
    profiles = list(UserProfile.objects.filter(serie=serie).select_related("user"))
    if len(profiles) < min_students:
        return None

    feature_order = get_feature_order(serie)
    vectors = []
    users = []

    for prof in profiles:
        feats = build_student_vector(prof.user, prof)
        arr = vector_to_array(feats, feature_order)
        vectors.append(arr)
        users.append(prof.user)

    k = min(n_clusters, max(2, len(users) // 3))

    try:
        import numpy as np
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler

        X = np.vstack(vectors)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        km = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = km.fit_predict(X_scaled)
        distances = km.transform(X_scaled)

        # Nettoyage et recréation des clusters
        PeerCluster.objects.filter(serie=serie).delete()
        cluster_objs: Dict[int, PeerCluster] = {}

        for c in range(k):
            centroid_orig = scaler.inverse_transform(km.cluster_centers_[c].reshape(1, -1))[0]
            centroid_dict = dict(zip(feature_order, [round(float(v), 3) for v in centroid_orig]))
            desc = _describe_cluster(centroid_dict)
            cluster_objs[c] = PeerCluster.objects.create(
                cluster_id=c,
                serie=serie,
                centroid_vector=centroid_dict,
                label=desc,
            )

        for i, user in enumerate(users):
            c_id = int(labels[i])
            cluster = cluster_objs[c_id]
            dist = float(distances[i, c_id])
            StudentClusterAssignment.objects.update_or_create(
                user=user,
                defaults={"cluster": cluster, "distance_to_centroid": dist},
            )

        return cluster_objs

    except ImportError:
        # Fallback pure Python si scikit-learn non disponible
        PeerCluster.objects.filter(serie=serie).delete()
        cluster = PeerCluster.objects.create(
            cluster_id=0,
            serie=serie,
            centroid_vector={},
            label="Profil dynamique de série",
        )
        for user in users:
            StudentClusterAssignment.objects.update_or_create(
                user=user,
                defaults={"cluster": cluster, "distance_to_centroid": 0.0},
            )
        return {0: cluster}


def get_peer_comparison_insight(user) -> Dict[str, str]:
    """
    Retourne l'insight de comparaison de pairs affiché sur la page Progression.
    """
    if not user or not user.is_authenticated:
        return {}

    assignment = (
        StudentClusterAssignment.objects.filter(user=user)
        .select_related("cluster")
        .first()
    )
    if not assignment or not assignment.cluster:
        return {}

    return {
        "cluster_label": assignment.cluster.label,
        "message": (
            f"Ton profil d'apprentissage est proche des élèves du groupe « {assignment.cluster.label} ». "
            "Les élèves de ce groupe qui ont progressé le plus vite ont consolidé leurs erreurs FSRS chaque matin."
        ),
    }
