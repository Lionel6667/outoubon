"""
Agrégation profonde du profil d'apprentissage pour le coaching (0 IA).
Centralise quiz, exos, cours, erreurs SM-2, diagnostic, chat, fiches.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set

from django.utils import timezone

from accounts.models import UserProfile, DiagnosticResult


def build_coaching_context(user, mats: dict, get_user_serie_subjects_fn) -> Dict[str, Any]:
    """
    Collecte toutes les signaux disponibles en base pour un élève.
    mats: dict MATS depuis views ; get_user_serie_subjects_fn: callable(user) -> set
    """
    from core.models import (
        AIMemory,
        ChatMessage,
        ChatSessionSummary,
        CourseSession,
        FlashcardProgress,
        LearningEvent,
        MistakeTracker,
        QuizSession,
        SubjectMastery,
        UserStats,
    )

    today = timezone.localdate()
    week_ago = today - timedelta(days=7)

    profile, _ = UserProfile.objects.get_or_create(user=user)
    stats, _ = UserStats.objects.get_or_create(user=user)
    user_subjs: Set[str] = set(get_user_serie_subjects_fn(user) or [])

    # ── SubjectMastery ───────────────────────────────────────────────
    mastery_map: Dict[str, SubjectMastery] = {}
    for sm in SubjectMastery.objects.filter(user=user):
        if user_subjs and sm.subject not in user_subjs:
            continue
        mastery_map[sm.subject] = sm

    # ── Quiz sessions (avec details JSON des réponses) ─────────────────
    recent_sessions = list(
        QuizSession.objects.filter(user=user).order_by('-completed_at')[:80]
    )
    sessions_by_subj: Dict[str, list] = defaultdict(list)
    recent_quiz_wrongs: List[dict] = []
    for rs in recent_sessions:
        if rs.subject in mats and len(sessions_by_subj[rs.subject]) < 15:
            sessions_by_subj[rs.subject].append(rs)
        for d in (rs.details or []):
            if d.get('ok'):
                continue
            q = (d.get('question') or d.get('enonce') or '').strip()
            if not q:
                continue
            ua = str(d.get('user_answer', d.get('answer', '')) or '').strip()
            opts = d.get('options') or []
            rc = d.get('reponse_correcte', 0)
            try:
                rc_idx = int(rc)
                correct_txt = opts[rc_idx] if isinstance(opts, list) and 0 <= rc_idx < len(opts) else ''
            except (TypeError, ValueError, IndexError):
                correct_txt = ''
            recent_quiz_wrongs.append({
                'subject': rs.subject,
                'question': q[:220],
                'user_answer': ua[:120],
                'correct_answer': str(correct_txt)[:120],
                'theme': str(d.get('theme') or d.get('sujet') or ''),
                'date': rs.completed_at.date().isoformat(),
                'session_id': rs.pk,
            })
    recent_quiz_wrongs = recent_quiz_wrongs[:25]

    # ── MistakeTracker (SM-2) ──────────────────────────────────────────
    all_mistakes = list(
        MistakeTracker.objects.filter(user=user, mastered=False).order_by('-wrong_count', 'next_review')
    )
    if user_subjs:
        all_mistakes = [m for m in all_mistakes if m.subject in user_subjs]
    mistakes_due_today = [m for m in all_mistakes if m.next_review <= today]
    mistakes_by_theme: Counter = Counter()
    mistakes_by_subject: Counter = Counter()
    for m in all_mistakes:
        if m.theme:
            mistakes_by_theme[m.theme] += m.wrong_count
        mistakes_by_subject[m.subject] += m.wrong_count

    # ── Learning events (quiz, exo, cours) ─────────────────────────────
    recent_events = list(
        LearningEvent.objects.filter(user=user, created_at__date__gte=week_ago).order_by('-created_at')[:60]
    )
    events_today: Counter = Counter()
    recent_exercise_low: List[dict] = []
    recent_quiz_events: List[dict] = []
    for ev in recent_events:
        if ev.created_at.date() == today:
            events_today[ev.event_type] += 1
        if ev.event_type == 'exercise_corrected':
            det = ev.details or {}
            pct = ev.score_pct or det.get('pct')
            if pct is not None and float(pct) < 55:
                recent_exercise_low.append({
                    'subject': ev.subject,
                    'pct': float(pct),
                    'title': (det.get('exercise_title') or det.get('title') or '')[:100],
                    'wrong_samples': det.get('wrong_samples') or [],
                    'date': ev.created_at.date().isoformat(),
                })
        elif ev.event_type == 'quiz_completed':
            recent_quiz_events.append({
                'subject': ev.subject,
                'score_pct': ev.score_pct,
                'details': ev.details or {},
                'date': ev.created_at.date().isoformat(),
            })

    # ── Cours interactifs ──────────────────────────────────────────────
    course_sessions = list(
        CourseSession.objects.filter(user=user).order_by('-updated_at')[:40]
    )
    courses_active: List[dict] = []
    courses_stalled: List[dict] = []
    courses_completed_week: List[dict] = []
    for cs in course_sessions:
        subj = cs.get_chapter_subject()
        if user_subjs and subj and subj not in user_subjs:
            continue
        entry = {
            'subject': subj,
            'title': cs.get_chapter_title()[:80],
            'progress_step': cs.progress_step,
            'status': cs.status,
            'updated': cs.updated_at.date(),
            'days_since': (today - cs.updated_at.date()).days,
            'url_subject': subj,
            'chapter_num': cs.chapter_num or 1,
        }
        if cs.status == 'active':
            courses_active.append(entry)
            if entry['days_since'] >= 5:
                courses_stalled.append(entry)
        elif cs.status == 'completed' and cs.updated_at.date() >= week_ago:
            courses_completed_week.append(entry)

    # ── Diagnostic initial ─────────────────────────────────────────────
    diagnostic_scores: Dict[str, int] = {}
    for d in DiagnosticResult.objects.filter(user=user):
        diagnostic_scores[d.subject] = int(d.score)

    # ── AIMemory + résumés chat ────────────────────────────────────────
    memories = list(
        AIMemory.objects.filter(user=user, memory_type__in=['erreur', 'concept'])
        .order_by('-importance', '-updated_at')[:8]
    )
    chat_summaries = list(
        ChatSessionSummary.objects.filter(user=user).order_by('-created_at')[:5]
    )
    chat_today = ChatMessage.objects.filter(
        user=user, role='user', created_at__date=today,
    ).count()

    # ── Fiches mémo à revoir ───────────────────────────────────────────
    flashcards_review = FlashcardProgress.objects.filter(
        user=user, status='review',
    ).count()

    # ── Topic errors depuis mastery ────────────────────────────────────
    topic_error_counts: Dict[tuple, int] = {}
    for subj, sm in mastery_map.items():
        for err in (sm.recent_errors or []):
            topic = (err.get('topic') or '').strip()
            if topic:
                key = (subj, topic)
                topic_error_counts[key] = topic_error_counts.get(key, 0) + 1

    # ── subject_data (scores unifiés + tendance quiz) ───────────────────
    from core.subject_scores import compute_all_blended_scores
    all_blended = compute_all_blended_scores(user)

    subject_data: Dict[str, dict] = {}
    for subj in mats:
        sessions = sessions_by_subj.get(subj, [])
        pcts = [round((s.score / s.total) * 100) for s in sessions if s.total]
        trend = 0
        if len(pcts) >= 4:
            recent_avg = sum(pcts[:3]) / 3
            older_avg = sum(pcts[3:]) / max(1, len(pcts[3:]))
            trend = round(recent_avg - older_avg)
        last_session_date = sessions[0].completed_at.date() if sessions else None
        sm = mastery_map.get(subj)
        blended = all_blended.get(subj, {}).get('blended')
        if blended is not None:
            avg = int(blended)
        elif sm:
            avg = int(round(sm.mastery_score))
        elif pcts:
            avg = round(sum(pcts) / len(pcts))
        else:
            avg = diagnostic_scores.get(subj, 0)
        subject_data[subj] = {
            'avg': avg,
            'blended': blended,
            'trend': trend,
            'count': len(pcts),
            'last': last_session_date,
            'pcts': pcts,
            'mastery': sm,
        }

    due_by_subj = defaultdict(int)
    for m in mistakes_due_today:
        due_by_subj[m.subject] += 1

    return {
        'today': today,
        'profile': profile,
        'stats': stats,
        'user_subjs': user_subjs,
        'mastery_map': mastery_map,
        'subject_data': subject_data,
        'sessions_by_subj': dict(sessions_by_subj),
        'recent_quiz_wrongs': recent_quiz_wrongs,
        'mistakes_due_today': mistakes_due_today,
        'mistakes_all': all_mistakes[:30],
        'mistakes_by_theme': mistakes_by_theme,
        'mistakes_by_subject': mistakes_by_subject,
        'total_due': len(mistakes_due_today),
        'total_mistakes': len(all_mistakes),
        'total_mastered': MistakeTracker.objects.filter(user=user, mastered=True).count(),
        'due_by_subj': dict(due_by_subj),
        'recent_exercise_low': recent_exercise_low[:8],
        'recent_quiz_events': recent_quiz_events[:10],
        'events_today': dict(events_today),
        'chat_messages_today': chat_today,
        'courses_active': courses_active,
        'courses_stalled': courses_stalled,
        'courses_completed_week': courses_completed_week,
        'diagnostic_scores': diagnostic_scores,
        'memories': memories,
        'chat_summaries': chat_summaries,
        'flashcards_review': flashcards_review,
        'topic_error_counts': topic_error_counts,
    }


def append_hyper_coaching_cards(cards: list, ctx: dict, mats: dict, helpers: dict) -> list:
    """
    Ajoute des cartes ultra-ciblées depuis le contexte riche.
    helpers: {_pick_clear_topic, _clean_topic_name, get_subject_chapters, _has_resources}
    """
    _pick_clear_topic = helpers.get('_pick_clear_topic')
    _clean = helpers.get('_clean_topic_name', lambda x: x)
    _has_resources = helpers.get('_has_resources', False)
    get_chapters = helpers.get('get_subject_chapters')

    used_ids = {c['id'] for c in cards}
    used_subjects = set()
    for c in cards:
        if '_' in c.get('id', ''):
            used_subjects.add(c['id'].rsplit('_', 1)[-1])

    def _add(card: dict):
        if card['id'] in used_ids:
            return
        cards.append(card)
        used_ids.add(card['id'])
        if '_' in card['id']:
            used_subjects.add(card['id'].rsplit('_', 1)[-1])

    today = ctx['today']

    # ── Dernière mauvaise réponse quiz (texte exact) ───────────────────
    for wrong in ctx.get('recent_quiz_wrongs', [])[:3]:
        subj = wrong['subject']
        if subj in used_subjects:
            continue
        info = mats.get(subj, {})
        label = info.get('label', subj)
        color = info.get('color', '#ef4444')
        q_preview = wrong['question'][:90]
        ua = wrong.get('user_answer') or ''
        desc_parts = [f'Question ratée : <em>« {q_preview}{"…" if len(wrong["question"]) > 90 else ""} »</em>']
        if ua:
            desc_parts.append(f'Ta réponse : <strong style="color:#fca5a5;">{ua[:80]}</strong>')
        if wrong.get('correct_answer'):
            desc_parts.append(f'Bonne réponse : <strong style="color:var(--green);">{wrong["correct_answer"][:80]}</strong>')
        theme = wrong.get('theme') or ''
        quiz_url = f'/dashboard/quiz/?subject={subj}'
        if theme:
            quiz_url += f'&chapter={theme}'
        _add({
            'id': f'quiz_wrong_{subj}',
            'type': 'quiz_wrong_detail',
            'icon': 'fas fa-times-circle',
            'color': color,
            'priority': 1,
            'title': f'{label} — erreur récente à corriger',
            'description': '. '.join(desc_parts) + '.',
            'action_label': 'Quiz ciblé',
            'action_url': quiz_url,
            'badge': '✗',
            'badge_color': '#ef4444',
        })
        break

    # ── Question SM-2 la plus ratée (due aujourd'hui) ──────────────────
    due = ctx.get('mistakes_due_today', [])
    if due and len(cards) < 6:
        top = max(due, key=lambda m: m.wrong_count)
        subj = top.subject
        if f'quiz_wrong_{subj}' not in used_ids and subj not in used_subjects:
            info = mats.get(subj, {})
            label = info.get('label', subj)
            opts = top.options or []
            try:
                good = opts[int(top.reponse_correcte)] if opts else ''
            except (IndexError, TypeError, ValueError):
                good = ''
            expl = (top.explication or '')[:120]
            desc = f'Ratée <strong>{top.wrong_count}×</strong> — « {top.enonce[:85]}… »'
            if good:
                desc += f'<br>Bonne réponse : <strong style="color:var(--green);">{str(good)[:70]}</strong>'
            if expl:
                desc += f'<br><span style="font-size:.78rem;color:var(--t3);">{expl}…</span>'
            _add({
                'id': f'sm2_due_{top.pk}',
                'type': 'mistake_due',
                'icon': 'fas fa-redo',
                'color': '#f59e0b',
                'priority': 1,
                'title': f'{label} — révision due ({top.theme or "quiz"})',
                'description': desc,
                'action_label': 'Réviser cette question',
                'action_url': f'/dashboard/quiz/?subject={subj}',
                'badge': str(top.wrong_count),
                'badge_color': '#f59e0b',
            })

    # ── Exercice récent faible score ───────────────────────────────────
    for ex in ctx.get('recent_exercise_low', [])[:2]:
        subj = ex['subject']
        if any(c['id'].endswith(f'_{subj}') for c in cards):
            continue
        info = mats.get(subj, {})
        label = info.get('label', subj)
        title_hint = ex.get('title') or 'exercice'
        wrong_samples = ex.get('wrong_samples') or []
        extra = ''
        if wrong_samples:
            w = wrong_samples[0]
            extra = f'<br>Ex. : « {str(w.get("question", ""))[:70]}… »'
        _add({
            'id': f'exo_low_{subj}',
            'type': 'exo_weak',
            'icon': 'fas fa-pen-fancy',
            'color': info.get('color', '#10b981'),
            'priority': 1,
            'title': f'{label} — exo à {int(ex["pct"])}%',
            'description': (
                f'Sur <strong>{title_hint}</strong>, ton score est insuffisant ({int(ex["pct"])}%). '
                f'Relis la correction IA et retente un exercice similaire.{extra}'
            ),
            'action_label': 'Exercices',
            'action_url': f'/dashboard/exercices/?subject={subj}',
            'badge': f'{int(ex["pct"])}%',
            'badge_color': '#ef4444',
        })
        break

    # ── Cours en pause (session active mais abandonnée) ────────────────
    for cs in ctx.get('courses_stalled', [])[:1]:
        subj = cs['subject']
        info = mats.get(subj, {})
        _add({
            'id': f'course_stall_{subj}_{cs["chapter_num"]}',
            'type': 'course_stalled',
            'icon': 'fas fa-book-reader',
            'color': info.get('color', '#06b6d4'),
            'priority': 2,
            'title': f'{info.get("label", subj)} — cours en pause',
            'description': (
                f'Tu as commencé <strong>« {cs["title"]} »</strong> mais tu n\'y es pas retourné '
                f'depuis <strong>{cs["days_since"]} jours</strong>. Reprends où tu en étais (étape {cs["progress_step"]}/3).'
            ),
            'action_label': 'Continuer le cours',
            'action_url': f'/dashboard/cours/{subj}/',
            'badge': f'{cs["days_since"]}j',
            'badge_color': '#06b6d4',
        })

    # ── Diagnostic vs maîtrise actuelle (lacune initiale persistante) ──
    for subj, diag_score in ctx.get('diagnostic_scores', {}).items():
        if subj in used_subjects:
            continue
        sm = ctx['mastery_map'].get(subj)
        mastery_now = int(round(sm.mastery_score)) if sm else diag_score
        if diag_score < 55 and mastery_now < 55:
            info = mats.get(subj, {})
            _add({
                'id': f'diag_gap_{subj}',
                'type': 'diagnostic_weak',
                'icon': 'fas fa-stethoscope',
                'color': info.get('color', '#8b5cf6'),
                'priority': 2,
                'title': f'{info.get("label", subj)} — lacune du diagnostic',
                'description': (
                    f'Diagnostic initial : <strong>{diag_score}%</strong>, maîtrise actuelle : '
                    f'<strong>{mastery_now}%</strong>. Ce point faible persiste — priorité BAC.'
                ),
                'action_label': 'Travailler cette matière',
                'action_url': f'/dashboard/quiz/?subject={subj}',
                'badge': f'{mastery_now}%',
                'badge_color': '#8b5cf6',
            })
            break

    # ── Fiches mémo en attente ─────────────────────────────────────────
    fc = ctx.get('flashcards_review', 0)
    if fc >= 5 and len(cards) < 7:
        _add({
            'id': 'flashcards_review',
            'type': 'flashcards',
            'icon': 'fas fa-layer-group',
            'color': '#06b6d4',
            'priority': 2,
            'title': f'{fc} fiches à revoir',
            'description': (
                'Tu as des fiches mémo marquées « à revoir ». '
                '5 minutes de révision active la mémoire à long terme.'
            ),
            'action_label': 'Fiches mémo',
            'action_url': '/dashboard/fiches/',
            'badge': str(fc),
            'badge_color': '#06b6d4',
        })

    # ── Thème d'erreur récurrent (agrégat SM-2) ────────────────────────
    themes = ctx.get('mistakes_by_theme')
    if themes and len(cards) < 7:
        theme, count = themes.most_common(1)[0]
        # trouver matière dominante pour ce thème
        subj_for_theme = None
        for m in ctx.get('mistakes_all', []):
            if m.theme == theme:
                subj_for_theme = m.subject
                break
        if subj_for_theme and subj_for_theme not in used_subjects:
            info = mats.get(subj_for_theme, {})
            _add({
                'id': f'theme_repeat_{subj_for_theme}',
                'type': 'theme_pattern',
                'icon': 'fas fa-exclamation-circle',
                'color': '#ef4444',
                'priority': 1,
                'title': f'Pattern d\'erreur : {theme}',
                'description': (
                    f'Tu as accumulé <strong>{count} ratées</strong> sur le thème '
                    f'<strong>{theme}</strong> ({info.get("label", subj_for_theme)}). '
                    'Cible ce chapitre avant le prochain devoir.'
                ),
                'action_label': 'Cibler ce thème',
                'action_url': f'/dashboard/exercices/?subject={subj_for_theme}',
                'badge': str(count),
                'badge_color': '#ef4444',
            })

    # ── Résumé chat récent (faiblesses détectées en session) ───────────
    profile = ctx.get('profile')
    coach_label = ((getattr(profile, 'coach_name', None) or '').strip() or 'ton IA')
    summaries = ctx.get('chat_summaries', [])
    if summaries and len(cards) < 7:
        summ = summaries[0].summary or {}
        weaknesses = summ.get('weaknesses') or []
        if weaknesses:
            wtxt = weaknesses[0] if isinstance(weaknesses[0], str) else str(weaknesses[0])
            subjs = summaries[0].subjects_covered or []
            subj = subjs[0] if subjs else 'general'
            _add({
                'id': 'chat_summary_weak',
                'type': 'chat_insight',
                'icon': 'fas fa-comment-medical',
                'color': '#a78bfa',
                'priority': 2,
                'title': 'Lacune vue en chat IA',
                'description': (
                    f'Dans ta dernière session avec {coach_label}, tu as montré une difficulté sur : '
                    f'<strong>{wtxt[:100]}</strong>. Clarifie ce point maintenant.'
                ),
                'action_label': f'Demander à {coach_label}',
                'action_url': f'/dashboard/chat/?subject={subj}',
                'badge': None,
                'badge_color': None,
            })

    return cards
