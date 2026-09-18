"""
Package ML & Psychométrie pour OU TOU BON.
Contient :
- IRT 2PL (irt_engine.py, irt_calibration.py)
- BKT (bkt_engine.py)
- FSRS (fsrs_engine.py)
- Graphe de prérequis & causalité (prerequisite_graph.py)
- Vecteur élève unifié (student_vector.py)
- Clustering de pairs k-means (peer_clustering.py)
- Modèle de risque BAC (risk_predictor.py)
- Bandit contextuel Thompson Sampling (content_bandit.py)
- Ordonnanceur Sac-à-Dos (revision_scheduler.py)
- Compilation de la note BAC & Simulation Monte Carlo (bac_score_engine.py)
- Moteur d'exercices adaptatifs & Examens Blancs (exercises_engine.py)
"""
from core.ml.irt_engine import probability_correct, update_student_theta, theta_to_display_score
from core.ml.bkt_engine import bkt_update, record_response_and_update_mastery, get_weak_topics
from core.ml.fsrs_engine import retrievability, update_card_after_review, get_due_cards
from core.ml.prerequisite_graph import diagnose_root_cause, build_action_card
from core.ml.student_vector import build_student_vector, vector_to_array
from core.ml.peer_clustering import run_clustering_for_serie, get_peer_comparison_insight
from core.ml.risk_predictor import predict_risk, train_risk_model
from core.ml.content_bandit import select_arm_thompson_sampling, record_arm_outcome
from core.ml.revision_scheduler import build_session_plan, build_weekly_plan
from core.ml.bac_score_engine import compile_full_bac_report, simulate_intervention, simulate_bac_score_distribution
from core.ml.exercises_engine import record_exercise_attempt, get_exercise_recommendations, track_exam_item_outcome
