"""
learning_tracker.py — Système de suivi adaptatif de l'apprentissage.

Fonctions:
  update_subject_mastery()         — Mise à jour de la maîtrise après chaque réponse
  log_learning_event()             — Enregistrement d'un événement d'apprentissage
  generate_and_save_chat_summary() — Génération + sauvegarde du résumé de session chat (async)
  get_mastery_profile_text()       — Texte structuré de la maîtrise pour le prompt IA
"""

from __future__ import annotations
import json
from datetime import date, datetime
from typing import Optional


# ─── Constantes ───────────────────────────────────────────────────────────────

# Poids de l'EMA (Exponential Moving Average) pour le score de maîtrise
# Valeur haute = apprentissage plus rapide, réponses récentes pèsent plus
EMA_ALPHA = 0.25

# Max d'éléments dans recent_errors et recent_correct
# Pas de limite — toutes les erreurs et bonnes réponses sont conservées

CONFIDENCE_THRESHOLDS = {
    'expert':         85,
    'avance':         70,
    'intermediaire':  50,
    'apprenti':       25,
    'debutant':        0,
}

MATS_LABELS = {
    'maths':       'Mathématiques',
    'physique':    'Physique',
    'chimie':      'Chimie',
    'svt':         'SVT',
    'francais':    'Kreyòl',
    'philosophie': 'Philosophie',
    'histoire':    'Histoire',
    'anglais':     'Anglais',
    'espagnol':    'Espagnol',
    'economie':    'Économie',
    'informatique':'Informatique',
    'art':         'Arts',
    'general':     'Général',
}


# ─── Mise à jour de la maîtrise ───────────────────────────────────────────────

def update_subject_mastery(
    user,
    subject: str,
    is_correct: bool,
    question_text: str = '',
    answer_text: str = '',
    error_type: str = '',
    score_pct: Optional[float] = None,
    topic: str = '',
) -> None:
    """
    Met à jour SubjectMastery pour un utilisateur/matière après une réponse.

    - is_correct  : True si la réponse est correcte, False sinon
    - question_text : texte de la question (pour recent_errors / recent_correct)
    - answer_text  : réponse de l'élève
    - error_type   : type d'erreur (ex: 'calcul', 'concept', 'lecture')
    - score_pct    : score en pourcentage si exercice complet (remplace is_correct si fourni)
    - topic        : thème/chapitre lié (ex: 'Dérivées', 'Condensateur')
    """
    try:
        from .models import SubjectMastery

        sm, _ = SubjectMastery.objects.get_or_create(
            user=user, subject=subject
        )

        today_str = date.today().isoformat()

        # Calcul du score courant (0 ou 100 pour une réponse, ou score_pct pour un exercice)
        if score_pct is not None:
            current_score = float(score_pct)
            is_correct_for_count = score_pct >= 60
        else:
            current_score = 100.0 if is_correct else 0.0
            is_correct_for_count = is_correct

        # Mise à jour de l'EMA du score de maîtrise
        if sm.total_attempts() == 0:
            sm.mastery_score = current_score
        else:
            sm.mastery_score = (EMA_ALPHA * current_score) + ((1 - EMA_ALPHA) * sm.mastery_score)

        # Compteurs globaux
        if is_correct_for_count:
            sm.correct_count += 1
        else:
            sm.error_count += 1

        # Historique des erreurs récentes
        if not is_correct_for_count and question_text:
            entry = {
                'question': question_text[:200],
                'date': today_str,
                'error_type': error_type or 'general',
                'topic': topic or '',
            }
            errors = list(sm.recent_errors)
            errors.insert(0, entry)
            sm.recent_errors = errors

            # Mise à jour des topics faibles
            if topic:
                weak = list(sm.weak_topics)
                if topic not in weak:
                    weak.insert(0, topic)
                sm.weak_topics = weak[:20]

        # Historique des bonnes réponses récentes
        if is_correct_for_count and question_text:
            entry = {
                'question': question_text[:200],
                'answer': answer_text[:200],
                'date': today_str,
                'topic': topic or '',
            }
            correct = list(sm.recent_correct)
            correct.insert(0, entry)
            sm.recent_correct = correct

            # Si ce topic était faible et qu'on a 3 bonnes réponses dessus → maîtrisé
            if topic and topic in sm.weak_topics:
                # Compter les bonnes réponses récentes sur ce topic
                recent_correct_topic = sum(
                    1 for c in sm.recent_correct if c.get('topic') == topic
                )
                if recent_correct_topic >= 3:
                    weak = list(sm.weak_topics)
                    if topic in weak:
                        weak.remove(topic)
                    sm.weak_topics = weak
                    mastered = list(sm.mastered_topics)
                    if topic not in mastered:
                        mastered.insert(0, topic)
                    sm.mastered_topics = mastered[:30]

        # Mise à jour du niveau de confiance
        sm.update_confidence_level()

        sm.save()

    except Exception as e:
        print(f"[LEARNING_TRACKER] update_subject_mastery error: {e}")


# ─── Enregistrement d'un événement d'apprentissage ────────────────────────────

def log_learning_event(
    user,
    event_type: str,
    subject: str,
    details: dict,
    score_pct: Optional[float] = None,
) -> None:
    """
    Enregistre un événement dans LearningEvent.
    event_type: 'quiz_completed' | 'exercise_corrected' | 'chat_session' | 'course_chapter' | 'mastery_gained'
    """
    try:
        from .models import LearningEvent
        LearningEvent.objects.create(
            user=user,
            event_type=event_type,
            subject=subject,
            details=details,
            score_pct=score_pct,
        )
    except Exception as e:
        print(f"[LEARNING_TRACKER] log_learning_event error: {e}")


# ─── Génération + sauvegarde du résumé de session chat ───────────────────────

def generate_and_save_chat_summary(user, session_key: str) -> None:
    """
    Génère un résumé JSON structuré de la session de chat et le sauvegarde.
    Conçu pour être appelé dans un thread daemon (non-bloquant).
    Ne remplace pas les anciens résumés — accumule.
    """
    try:
        from .models import ChatMessage, ChatSessionSummary, SubjectMastery
        from . import gemini as _gemini

        # Récupérer les messages de la session
        messages = list(
            ChatMessage.objects.filter(
                user=user, session_key=session_key
            ).order_by('created_at')
        )

        if len(messages) < 2:
            return  # Pas assez de messages pour un résumé utile

        # Vérifier si un résumé existe déjà pour cette session
        existing = ChatSessionSummary.objects.filter(user=user, session_key=session_key).first()
        if existing and existing.message_count >= len(messages):
            return  # Déjà à jour
        if existing and len(messages) - existing.message_count < 3:
            return  # Pas assez de nouveaux messages pour re-résumer

        # Construire le texte de la conversation
        conv_lines = []
        subjects_in_session = set()
        for msg in messages:
            role_label = "Élève" if msg.role == 'user' else "Coach IA"
            conv_lines.append(f"{role_label}: {msg.content[:500]}")
            if msg.subject and msg.subject != 'general':
                subjects_in_session.add(msg.subject)

        conv_text = '\n'.join(conv_lines)
        user_name = user.first_name or user.username

        # Appel IA pour générer le résumé
        summary_json = _gemini.generate_chat_summary_ai(conv_text, user_name)

        if not summary_json:
            return

        # Ajouter les matières détectées dans les messages
        if subjects_in_session:
            summary_json['subjects'] = list(subjects_in_session)

        # Sauvegarder ou mettre à jour le résumé
        if existing:
            existing.summary = summary_json
            existing.subjects_covered = summary_json.get('subjects', list(subjects_in_session))
            existing.message_count = len(messages)
            existing.save(update_fields=['summary', 'subjects_covered', 'message_count'])
        else:
            ChatSessionSummary.objects.create(
                user=user,
                session_key=session_key,
                summary=summary_json,
                subjects_covered=summary_json.get('subjects', list(subjects_in_session)),
                message_count=len(messages),
            )

        # Mettre à jour SubjectMastery basé sur les observations du résumé
        _apply_summary_to_mastery(user, summary_json, subjects_in_session)

    except Exception as e:
        print(f"[LEARNING_TRACKER] generate_and_save_chat_summary error: {e}")


def _apply_summary_to_mastery(user, summary: dict, subjects: set) -> None:
    """
    Applique les observations du résumé au profil de maîtrise.
    Utilisé seulement pour mettre à jour les weak_topics/mastered_topics.
    """
    try:
        from .models import SubjectMastery

        weaknesses = summary.get('weaknesses', [])
        strengths  = summary.get('strengths', [])

        for subj in subjects:
            sm, _ = SubjectMastery.objects.get_or_create(user=user, subject=subj)
            changed = False

            for w in weaknesses:
                if isinstance(w, str) and w not in sm.weak_topics:
                    sm.weak_topics = (sm.weak_topics or []) + [w]
                    changed = True

            for s in strengths:
                if isinstance(s, str) and s not in sm.mastered_topics:
                    sm.mastered_topics = (sm.mastered_topics or []) + [s]
                    changed = True

            if changed:
                sm.save(update_fields=['weak_topics', 'mastered_topics'])

    except Exception as e:
        print(f"[LEARNING_TRACKER] _apply_summary_to_mastery error: {e}")


# ─── Texte de profil de maîtrise pour le prompt IA ───────────────────────────

def get_mastery_profile_text(user) -> str:
    """
    Génère un bloc texte structuré décrivant la maîtrise par matière.
    Inclut : score, niveau, erreurs récentes, topics maîtrisés/faibles.
    Destiné à être injecté dans le prompt IA via build_user_learning_profile().
    """
    try:
        from .models import SubjectMastery, ChatSessionSummary

        masteries = list(SubjectMastery.objects.filter(user=user).order_by('-mastery_score'))
        summaries = list(ChatSessionSummary.objects.filter(user=user).order_by('-created_at')[:5])

        if not masteries and not summaries:
            return ''

        lines = []

        if masteries:
            lines.append("\n=== 🎯 MAÎTRISE PAR MATIÈRE (suivi adaptatif) ===")
            for sm in masteries:
                label = MATS_LABELS.get(sm.subject, sm.subject)
                accuracy = sm.accuracy_pct()
                emoji = '🟢' if sm.mastery_score >= 70 else '🟡' if sm.mastery_score >= 40 else '🔴'
                lines.append(
                    f"  {emoji} {label:<18} maîtrise={sm.mastery_score:.0f}%  "
                    f"({sm.correct_count}✓ / {sm.error_count}✗)  "
                    f"niveau={sm.confidence_level}"
                )
                if sm.weak_topics:
                    lines.append(f"     ⚠️  Points faibles : {', '.join(sm.weak_topics[:5])}")
                if sm.mastered_topics:
                    lines.append(f"     ✅ Maîtrisés : {', '.join(sm.mastered_topics[:5])}")
                if sm.recent_errors:
                    last_err = sm.recent_errors[0]
                    lines.append(
                        f"     📌 Dernière erreur ({last_err.get('date','')}) : "
                        f"{last_err.get('question','')[:80]}..."
                    )

        if summaries:
            lines.append("\n=== 📝 RÉSUMÉS DES SESSIONS DE CHAT (5 dernières) ===")
            for s in summaries:
                summ = s.summary
                date_str = s.created_at.strftime('%d/%m/%Y')
                subjs = ', '.join(MATS_LABELS.get(x, x) for x in (s.subjects_covered or []))
                lines.append(f"\n  📅 Session du {date_str} — {s.message_count} messages — [{subjs or 'Général'}]")
                if summ.get('confidence'):
                    lines.append(f"     Confiance élève : {summ['confidence']}")
                if summ.get('strengths'):
                    pts = summ['strengths']
                    if isinstance(pts, list):
                        pts = ', '.join(pts[:3])
                    lines.append(f"     ✅ Points forts : {pts}")
                if summ.get('weaknesses'):
                    pts = summ['weaknesses']
                    if isinstance(pts, list):
                        pts = ', '.join(pts[:3])
                    lines.append(f"     ⚠️  Points faibles : {pts}")
                if summ.get('observations'):
                    obs = summ['observations']
                    if isinstance(obs, list):
                        obs = ' | '.join(obs[:2])
                    lines.append(f"     🔍 Observations : {obs}")

        return '\n'.join(lines)

    except Exception as e:
        print(f"[LEARNING_TRACKER] get_mastery_profile_text error: {e}")
        return ''


# ─── Contexte adaptatif pour les exercices/quiz ───────────────────────────────

def get_adaptive_level(user, subject: str) -> str:
    """
    Retourne le niveau adaptatif d'un utilisateur pour une matière.
    Utilisé pour calibrer la difficulté des exercices et quiz.
    Returns: 'debutant' | 'apprenti' | 'intermediaire' | 'avance' | 'expert'
    """
    try:
        from .models import SubjectMastery
        sm = SubjectMastery.objects.get(user=user, subject=subject)
        return sm.confidence_level
    except Exception:
        return 'intermediaire'


def get_study_recommendations(user) -> list[dict]:
    """
    Génère des recommandations d'étude basées sur la maîtrise.
    Returns liste de {subject, label, priority, reason, action}
    """
    try:
        from .models import SubjectMastery
        masteries = list(SubjectMastery.objects.filter(user=user))
        recs = []

        for sm in sorted(masteries, key=lambda x: x.mastery_score):
            if sm.mastery_score < 50:
                priority = 'haute' if sm.mastery_score < 30 else 'moyenne'
                label = MATS_LABELS.get(sm.subject, sm.subject)
                weak = sm.weak_topics[:3] if sm.weak_topics else []
                reason = f"Score de maîtrise faible ({sm.mastery_score:.0f}%)"
                if weak:
                    reason += f" — topics à retravailler : {', '.join(weak)}"
                recs.append({
                    'subject': sm.subject,
                    'label': label,
                    'priority': priority,
                    'reason': reason,
                    'mastery': round(sm.mastery_score),
                    'action': 'quiz',
                })

        return recs[:5]

    except Exception as e:
        print(f"[LEARNING_TRACKER] get_study_recommendations error: {e}")
        return []
