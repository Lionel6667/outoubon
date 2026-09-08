"""
Profil élève agrégé — 0 IA, précision maximale pour coaching, missions, progression.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone

from core.coaching_context import build_coaching_context
from core.subject_scores import compute_all_blended_scores, estimate_bac_score, get_scores_for_user


def build_student_profile(user, mats: dict, get_user_serie_subjects_fn) -> Dict[str, Any]:
  from accounts.models import UserProfile

  ctx = build_coaching_context(user, mats, get_user_serie_subjects_fn)
  profile, _ = UserProfile.objects.get_or_create(user=user)
  user_subjs = set(ctx.get('user_subjs') or get_user_serie_subjects_fn(user) or [])
  diag = ctx.get('diagnostic_scores') or {}
  scores = get_scores_for_user(user, user_subjs, diag)
  all_blended = compute_all_blended_scores(user)

  from core.views import SERIES
  serie_key = profile.serie or 'SVT'
  bac_estimate = estimate_bac_score(scores, serie_key, SERIES)

  sorted_scores = sorted(scores.items(), key=lambda x: x[1])
  sorted_desc = sorted(scores.items(), key=lambda x: x[1], reverse=True)
  weaknesses = [(s, v) for s, v in sorted_scores if v < 65][:4]
  strengths = [(s, v) for s, v in sorted_desc if v >= 70][:3]
  if not weaknesses and sorted_scores:
    weaknesses = sorted_scores[:2]

  week_ago = ctx['today'] - timedelta(days=7)
  from core.models import ChatMessage
  chat_questions = list(
      ChatMessage.objects.filter(user=user, role='user', created_at__date__gte=week_ago)
      .order_by('-created_at')[:20]
  )
  chat_by_subject: Counter = Counter()
  chat_samples: List[dict] = []
  for msg in chat_questions:
      subj = (msg.subject or 'general').strip()
      chat_by_subject[subj] += 1
      if len(chat_samples) < 8:
          chat_samples.append({
              'text': (msg.content or '')[:180],
              'subject': subj,
              'date': msg.created_at.date().isoformat(),
          })

  return {
      'profile': profile,
      'ctx': ctx,
      'scores': scores,
      'all_blended': all_blended,
      'bac_estimate': bac_estimate,
      'weaknesses': weaknesses,
      'strengths': strengths,
      'chat_questions_week': len(chat_questions),
      'chat_by_subject': dict(chat_by_subject),
      'chat_samples': chat_samples,
      'coach_name': (profile.coach_name or '').strip() or 'ton coach',
      'display_name': (profile.first_name or user.first_name or user.username or 'toi').strip(),
  }


def add_friend_coaching_cards(cards: list, profile_data: Dict[str, Any], mats: dict) -> list:
    """Cartes ton « ami qui connaît tout » + salutation personnalisée."""
    name = profile_data.get('display_name', 'toi')
    ctx = profile_data.get('ctx', {})
    scores = profile_data.get('scores', {})
    coach = profile_data.get('coach_name', 'ton coach')

    used_ids = {c.get('id') for c in cards}

    # Synthèse accueil
    quiz_wrongs = len(ctx.get('recent_quiz_wrongs', []))
    exo_low = len(ctx.get('recent_exercise_low', []))
    chat_n = profile_data.get('chat_questions_week', 0)
    mistakes_due = ctx.get('total_due', 0)
    stalled = len(ctx.get('courses_stalled', []))

    bits = []
    if quiz_wrongs:
        bits.append(f'<strong>{quiz_wrongs}</strong> question(s) de quiz à revoir')
    if exo_low:
        bits.append(f'<strong>{exo_low}</strong> exercice(s) faible(s) cette semaine')
    if mistakes_due:
        bits.append(f'<strong>{mistakes_due}</strong> erreur(s) SM-2 dues aujourd\'hui')
    if stalled:
        bits.append(f'<strong>{stalled}</strong> cours en pause')
    if chat_n:
        bits.append(f'<strong>{chat_n}</strong> question(s) posée(s) à {coach} cette semaine')

    if bits:
        summary = ' · '.join(bits)
    else:
        summary = 'Commence par un quiz ou un exercice — je m\'adapte dès la première activité.'

    weakest = profile_data.get('weaknesses', [])
    weak_label = mats.get(weakest[0][0], {}).get('label', '') if weakest else ''
    weak_pct = weakest[0][1] if weakest else None

    greeting = {
        'id': 'friend_greeting',
        'type': 'friend',
        'icon': 'fas fa-hand-sparkles',
        'color': '#10b981',
        'priority': 0,
        'title': f'Coucou {name} — je suis à jour sur ton parcours',
        'description': (
            f'Voici ce que je vois : {summary}.'
            + (f' Priorité du moment : <strong>{weak_label}</strong> ({weak_pct}%).' if weak_label and weak_pct is not None else '')
        ),
        'action_label': 'Voir ma progression',
        'action_url': '/dashboard/progression/',
        'badge': None,
        'badge_color': None,
    }
    if 'friend_greeting' not in used_ids:
        cards.insert(0, greeting)

    # Question chat récente non couverte
    for sample in profile_data.get('chat_samples', [])[:2]:
        subj = sample['subject']
        if subj == 'general':
            continue
        cid = f'chat_q_{subj}_{sample["date"]}'
        if cid in used_ids or any(c['id'].endswith(f'_{subj}') for c in cards):
            continue
        info = mats.get(subj, {})
        cards.append({
            'id': cid,
            'type': 'chat_question',
            'icon': 'fas fa-comment-dots',
            'color': '#a78bfa',
            'priority': 1,
            'title': f'Tu as demandé à {coach} en {info.get("label", subj)}',
            'description': (
                f'« {sample["text"][:100]}{"…" if len(sample["text"]) > 100 else ""} » — '
                'Si c\'est encore flou, repasse un quiz ciblé ou le cours du chapitre.'
            ),
            'action_label': f'Chat {info.get("label", subj)}',
            'action_url': f'/dashboard/chat/?subject={subj}',
            'badge': 'Chat',
            'badge_color': '#a78bfa',
        })
        break

    return cards


def pick_personalized_missions(profile_data: Dict[str, Any], mats: dict) -> Dict[str, Any]:
    """Indices pour missions du jour sur mesure."""
    weaknesses = profile_data.get('weaknesses', [])
    ctx = profile_data.get('ctx', {})
    weak_subj = weaknesses[0][0] if weaknesses else None
    weak_label = mats.get(weak_subj, {}).get('label') if weak_subj else None

    due = ctx.get('mistakes_due_today', [])
    top_mistake = max(due, key=lambda m: m.wrong_count) if due else None

    stalled = ctx.get('courses_stalled', [])
    stall = stalled[0] if stalled else None

    return {
        'weak_subject': weak_subj,
        'weak_label': weak_label,
        'top_mistake_theme': top_mistake.theme if top_mistake else None,
        'stalled_course': stall,
        'chat_questions_week': profile_data.get('chat_questions_week', 0),
    }
