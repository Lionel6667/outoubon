"""
Missions du jour — génération serveur déterministe, 0 IA, sans modèle DB.
Complétion déduite des données existantes (quiz, exercices, chat, etc.).
"""
from __future__ import annotations

import hashlib
from datetime import date

from django.urls import reverse

from core.models import ChatMessage, LearningEvent, MistakeTracker, QuizSession
from core import xp_config as C


def _today():
    from django.utils import timezone
    return timezone.localdate()


_MONTHS_FR = (
    'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
    'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
)


def _format_date_fr(d: date) -> str:
    return f"{d.day} {_MONTHS_FR[d.month - 1]} {d.year}"


def _day_seed(user_id: int, day: date) -> int:
    raw = f"{day.isoformat()}:{user_id}"
    return int(hashlib.md5(raw.encode()).hexdigest(), 16)


def _activity_today(user):
    today = _today()
    quiz_count = QuizSession.objects.filter(user=user, completed_at__date=today).count()
    exo_count = LearningEvent.objects.filter(
        user=user, event_type='exercise_corrected', created_at__date=today,
    ).count()
    chat_count = ChatMessage.objects.filter(
        user=user, role='user', created_at__date=today,
    ).count()
    events_count = LearningEvent.objects.filter(user=user, created_at__date=today).count()
    mistakes_due = MistakeTracker.objects.filter(
        user=user, mastered=False, next_review__lte=today,
    ).count()
    return {
        'quiz': quiz_count,
        'exo': exo_count,
        'chat': chat_count,
        'events': events_count,
        'mistakes_due': mistakes_due,
    }


def _mission(
    id,
    title,
    description,
    icon,
    color,
    xp,
    url_name,
    completed,
    url_kwargs=None,
):
    url_kwargs = url_kwargs or {}
    try:
        url = reverse(url_name, kwargs=url_kwargs)
    except Exception:
        url = reverse('dashboard')
    return {
        'id': id,
        'title': title,
        'description': description,
        'icon': icon,
        'color': color,
        'xp': xp,
        'url': url,
        'completed': completed,
    }


def _pick_bonus_mission(user, profile, activity, weak_subject, weak_subject_label, mistakes_due):
    """Une mission bonus rotative, déterministe par jour + user."""
    today = _today()
    seed = _day_seed(user.id, today)
    weak_label = weak_subject_label or weak_subject or 'ta série'

    pool = []

    if weak_subject:
        pool.append({
            'id': 'bonus_weak_quiz',
            'title': f'Quiz {weak_label}',
            'description': f'Renforce {weak_label} — ta matière à surveiller aujourd\'hui.',
            'icon': 'fas fa-crosshairs',
            'color': '#3b82f6',
            'xp': C.XP_MISSION_BONUS_WEAK,
            'url_name': 'quiz',
            'done': activity['quiz'] >= 1,  # any quiz counts; weak-specific not tracked without DB
        })

    pool.append({
        'id': 'bonus_fiches',
        'title': 'Réviser des fiches mémo',
        'description': 'Parcours 5 fiches pour consolider tes acquis.',
        'icon': 'fas fa-layer-group',
        'color': '#06b6d4',
        'xp': 0,
        'url_name': 'fiches',
        'done': LearningEvent.objects.filter(
            user=user, event_type='course_chapter', created_at__date=today,
        ).exists(),
    })

    pool.append({
        'id': 'bonus_plan',
        'title': 'Suivre ton plan de révision',
        'description': 'Coche une tâche de ton plan hebdomadaire.',
        'icon': 'fas fa-calendar-check',
        'color': '#a78bfa',
        'xp': 0,
        'url_name': 'plan',
        'done': activity['events'] >= 4,
    })

    if mistakes_due > 0:
        pool.insert(0, {
            'id': 'bonus_mistakes',
            'title': f'Réviser {mistakes_due} erreur{"s" if mistakes_due > 1 else ""}',
            'description': 'Erreurs SM-2 en attente — repasse-les avant le BAC.',
            'icon': 'fas fa-redo',
            'color': '#f59e0b',
            'xp': C.XP_MISSION_BONUS_MISTAKES,
            'url_name': 'quiz',
            'done': activity['quiz'] >= 2,
        })

    pool.append({
        'id': 'bonus_active',
        'title': 'Session active',
        'description': 'Complète 3 activités (quiz, exo ou chat) dans la journée.',
        'icon': 'fas fa-fire',
        'color': '#f97316',
        'xp': 0,
        'url_name': 'dashboard',
        'done': (activity['quiz'] + activity['exo'] + activity['chat']) >= 3,
    })

    if not pool:
        pool.append({
            'id': 'bonus_explore',
            'title': 'Explorer la bibliothèque',
            'description': 'Consulte un examen PDF officiel.',
            'icon': 'fas fa-book-open',
            'color': '#64748b',
            'xp': 0,
            'url_name': 'library',
            'done': activity['events'] >= 2,
        })

    pick = pool[seed % len(pool)]
    return _mission(
        pick['id'],
        pick['title'],
        pick['description'],
        pick['icon'],
        pick['color'],
        pick['xp'],
        pick['url_name'],
        pick['done'],
    )


def build_daily_missions(
    user,
    profile,
    stats,
    weaknesses=None,
    exo_focus_subject=None,
    weak_subject_label=None,
    serie_label=None,
):
    """
    Retourne le pack missions du jour pour le template dashboard.
    weaknesses: liste [(subject, score)] du dashboard.
    """
    activity = _activity_today(user)
    weak_subject = exo_focus_subject
    if not weak_subject and weaknesses:
        weak_subject = weaknesses[0][0] if weaknesses else None

    coach = (getattr(profile, 'coach_name', None) or '').strip() or 'ton coach'
    weak_label = weak_subject_label or weak_subject
    serie_txt = serie_label or (getattr(profile, 'serie', None) or 'ta série')

    if weak_label and serie_label:
        quiz_desc = f'Priorité {weak_label} — lacune identifiée sur ta série {serie_txt}.'
    elif weak_label:
        quiz_desc = f'Renforce {weak_label}, ta matière la plus fragile aujourd\'hui.'
    else:
        quiz_desc = 'Questions tirées des vrais examens BAC de ta série.'

    quiz_title = f'Quiz {weak_label}' if weak_label else 'Lancer un quiz'

    if weak_label:
        exo_title = f'Exercice {weak_label}'
        exo_desc = f'Correction IA sur {weak_label} — cible tes points faibles.'
    else:
        exo_title = 'Résoudre un exercice'
        exo_desc = 'Correction IA + explication pas à pas.'

    if weak_label:
        chat_title = f'{coach} · {weak_label}'
        chat_desc = f'Pose une question sur {weak_label} à {coach}.'
    else:
        chat_title = f'Question à {coach}'
        chat_desc = f'{coach} connaît ta série et tes résultats récents.'

    missions = [
        _mission(
            'daily_quiz',
            quiz_title,
            quiz_desc,
            'fas fa-bolt',
            '#8b5cf6',
            C.XP_MISSION_QUIZ,
            'quiz',
            activity['quiz'] >= 1,
        ),
        _mission(
            'daily_exo',
            exo_title,
            exo_desc,
            'fas fa-pen',
            '#10b981',
            C.XP_MISSION_EXO,
            'exercices',
            activity['exo'] >= 1,
        ),
        _mission(
            'daily_chat',
            chat_title,
            chat_desc,
            'fas fa-robot',
            '#8b5cf6',
            C.XP_MISSION_CHAT,
            'chat',
            activity['chat'] >= 1,
        ),
        _pick_bonus_mission(user, profile, activity, weak_subject, weak_subject_label, activity['mistakes_due']),
    ]

    completed = sum(1 for m in missions if m['completed'])
    xp_core_total = C.XP_MISSION_QUIZ + C.XP_MISSION_EXO + C.XP_MISSION_CHAT
    from core.models import XpEvent
    from django.utils import timezone as _tz
    day_start = _tz.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    xp_earned_today = sum(
        int(e.amount or 0)
        for e in XpEvent.objects.filter(
            user=user, source=C.SOURCE_DAILY_MISSION, created_at__gte=day_start,
        ).only('amount')
    )

    return {
        'date_label': _format_date_fr(_today()),
        'items': missions,
        'completed': completed,
        'total': len(missions),
        'progress_pct': round(completed / len(missions) * 100) if missions else 0,
        'xp_earned_today': xp_earned_today,
        'xp_available_today': xp_core_total,
        'streak': getattr(profile, 'streak', 0) or 0,
    }


def build_guest_daily_missions():
    """Missions démo pour le mode visiteur."""
    try:
        quiz_url = reverse('quiz')
        exo_url = reverse('exercices')
        chat_url = reverse('chat')
        signup_url = reverse('signup')
    except Exception:
        quiz_url = exo_url = chat_url = signup_url = '/signup/'

    missions = [
        {
            'id': 'daily_quiz',
            'title': 'Lancer un quiz',
            'description': '3 quiz disponibles en mode démo.',
            'icon': 'fas fa-bolt',
            'color': '#8b5cf6',
            'xp': C.XP_MISSION_QUIZ,
            'url': quiz_url,
            'completed': True,
        },
        {
            'id': 'daily_exo',
            'title': 'Résoudre un exercice',
            'description': '2 exercices en démo — crée un compte pour plus.',
            'icon': 'fas fa-pen',
            'color': '#10b981',
            'xp': C.XP_MISSION_EXO,
            'url': exo_url,
            'completed': False,
        },
        {
            'id': 'daily_chat',
            'title': 'Discuter avec ton IA',
            'description': 'Chat IA limité en mode démo.',
            'icon': 'fas fa-robot',
            'color': '#8b5cf6',
            'xp': C.XP_MISSION_CHAT,
            'url': chat_url,
            'completed': False,
        },
        {
            'id': 'bonus_signup',
            'title': 'Créer ton compte',
            'description': 'Débloque missions, ligues et progression complète.',
            'icon': 'fas fa-user-plus',
            'color': '#f59e0b',
            'xp': 0,
            'url': signup_url,
            'completed': False,
        },
    ]
    completed = sum(1 for m in missions if m['completed'])
    return {
        'date_label': _format_date_fr(_today()),
        'items': missions,
        'completed': completed,
        'total': len(missions),
        'progress_pct': round(completed / len(missions) * 100),
        'xp_earned_today': C.XP_MISSION_QUIZ,
        'xp_available_today': C.XP_MISSION_QUIZ + C.XP_MISSION_EXO + C.XP_MISSION_CHAT,
        'streak': 0,
        'is_guest': True,
    }
