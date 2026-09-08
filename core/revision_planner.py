"""
Plan de révision hyper-personnalisé sans IA — coaching_context + chapitres réels + chapter_plans.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

WEEKDAYS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi']


def _priority(score: float) -> str:
    if score < 50:
        return 'high'
    if score < 70:
        return 'medium'
    return 'low'


def _duration_min(priority: str, signal_weight: int = 0) -> int:
    base = 75 if priority == 'high' else (60 if priority == 'medium' else 45)
    if signal_weight >= 10:
        return min(90, base + 15)
    return base


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').lower().strip())


def _word_overlap(a: str, b: str) -> int:
    aw = {w for w in _norm(a).split() if len(w) > 2}
    bw = {w for w in _norm(b).split() if len(w) > 2}
    return len(aw & bw)


def _get_chapter_catalog(subject: str) -> List[dict]:
    """Chapitres numérotés depuis les notes JSON."""
    try:
        from core.pdf_loader import get_chapters_from_note_json
        raw = get_chapters_from_note_json(subject) or []
    except Exception:
        raw = []
    chapters: List[dict] = []
    for ch in raw:
        num = ch.get('num') or ch.get('id') or (len(chapters) + 1)
        title = (ch.get('title') or '').strip()
        if title:
            chapters.append({'num': int(num), 'title': title})
    if not chapters:
        try:
            from core.resource_index import get_subject_chapters
            for i, t in enumerate(get_subject_chapters(subject) or [], start=1):
                if t.strip():
                    chapters.append({'num': i, 'title': t.strip()})
        except Exception:
            pass
    return chapters


def _match_chapter(subject: str, hint: str, chapters: List[dict]) -> Optional[dict]:
    if not chapters:
        return None
    if not hint or not hint.strip():
        return chapters[0]
    h = _norm(hint)
    for ch in chapters:
        t = _norm(ch['title'])
        if h == t or h in t or t in h:
            return ch
    best_ch, best_score = chapters[0], 0
    for ch in chapters:
        score = _word_overlap(hint, ch['title'])
        if score > best_score:
            best_score = score
            best_ch = ch
    return best_ch


def _match_quiz_category(subject: str, hint: str) -> str:
    try:
        from core.resource_index import get_quiz_categories
        cats = get_quiz_categories(subject) or []
    except Exception:
        cats = []
    if not cats:
        return ''
    if not hint:
        return cats[0]
    h = _norm(hint)
    for c in cats:
        cn = _norm(c)
        if h in cn or cn in h or _word_overlap(hint, c) >= 2:
            return c
    return cats[0]


def _pick_subtopics(subject: str, chapter_num: int, chapter_title: str, signals: List[dict], limit: int = 3) -> List[str]:
    try:
        from core.pdf_loader import get_chapter_plan
        plan_items = get_chapter_plan(subject, chapter_num, chapter_title) or []
    except Exception:
        plan_items = []

    picked: List[str] = []
    seen = set()

    for sig in sorted(signals, key=lambda s: -s.get('weight', 0)):
        theme = (sig.get('theme') or '').strip()
        if not theme:
            continue
        for item in plan_items:
            if _word_overlap(theme, item) >= 1 or _norm(theme) in _norm(item):
                key = _norm(item)
                if key not in seen:
                    seen.add(key)
                    picked.append(item[:90])
                    break
        if len(picked) >= limit:
            break

    for item in plan_items:
        key = _norm(item)
        if key not in seen and len(picked) < limit:
            seen.add(key)
            picked.append(item[:90])

    return picked[:limit]


def _collect_weakness_signals(ctx: dict, subj: str, today) -> List[dict]:
    signals: List[dict] = []

    for m in ctx.get('mistakes_all', []) or []:
        if m.subject != subj:
            continue
        due = m.next_review <= today
        signals.append({
            'weight': int(m.wrong_count) * 3 + (4 if due else 0),
            'theme': (m.theme or '').strip(),
            'source': 'sm2',
            'detail': f'SM-2 : ratée {m.wrong_count}×' + (' · due aujourd\'hui' if due else ''),
            'enonce': (m.enonce or '')[:100],
        })

    sm = ctx.get('mastery_map', {}).get(subj)
    if sm:
        mastery = int(round(sm.mastery_score))
        for t in (sm.weak_topics or [])[:6]:
            signals.append({
                'weight': 5,
                'theme': str(t),
                'source': 'mastery',
                'detail': f'Maîtrise adaptative {mastery}% — point faible',
            })
        if sm.recent_errors:
            err = sm.recent_errors[0]
            q = (err.get('question') or err.get('topic') or '')[:80]
            if q:
                signals.append({
                    'weight': 4,
                    'theme': (err.get('topic') or q),
                    'source': 'mastery_err',
                    'detail': f'Dernière erreur : « {q}… »',
                })

    for (s, topic), cnt in (ctx.get('topic_error_counts') or {}).items():
        if s == subj and topic:
            signals.append({
                'weight': min(12, cnt * 2),
                'theme': topic,
                'source': 'pattern',
                'detail': f'{cnt} erreurs sur « {topic} »',
            })

    for w in ctx.get('recent_quiz_wrongs', []) or []:
        if w.get('subject') != subj:
            continue
        signals.append({
            'weight': 4,
            'theme': (w.get('theme') or '').strip(),
            'source': 'quiz',
            'detail': f'Quiz raté : « {(w.get("question") or "")[:70]}… »',
        })

    for cs in ctx.get('courses_stalled', []) or []:
        if cs.get('subject') != subj:
            continue
        signals.append({
            'weight': 6,
            'theme': cs.get('title') or '',
            'source': 'stalled',
            'detail': f'Cours en pause {cs.get("days_since", 0)}j — étape {cs.get("progress_step", 0)}/3',
            'chapter_num': cs.get('chapter_num'),
        })

    for ex in ctx.get('recent_exercise_low', []) or []:
        if ex.get('subject') != subj:
            continue
        signals.append({
            'weight': 5,
            'theme': (ex.get('title') or '').strip(),
            'source': 'exo',
            'detail': f'Exercice BAC {int(ex.get("pct", 0))}% — à retravailler',
        })

    diag = (ctx.get('diagnostic_scores') or {}).get(subj)
    subj_data = (ctx.get('subject_data') or {}).get(subj) or {}
    if diag is not None and diag < 55:
        signals.append({
            'weight': 3,
            'theme': '',
            'source': 'diagnostic',
            'detail': f'Diagnostic initial {diag}% — lacune persistante',
        })
    if subj_data.get('trend', 0) < -8:
        signals.append({
            'weight': 3,
            'theme': '',
            'source': 'trend',
            'detail': f'Tendance quiz en baisse ({subj_data.get("trend")} pts)',
        })

    summaries = ctx.get('chat_summaries') or []
    if summaries:
        summ = summaries[0].summary or {}
        weaknesses = summ.get('weaknesses') or []
        subjs_cov = summaries[0].subjects_covered or []
        if weaknesses and (subj in subjs_cov or not subjs_cov):
            wtxt = weaknesses[0] if isinstance(weaknesses[0], str) else str(weaknesses[0])
            signals.append({
                'weight': 4,
                'theme': wtxt[:80],
                'source': 'chat',
                'detail': f'Chat IA : difficulté sur « {wtxt[:60]} »',
            })

    return sorted(signals, key=lambda x: -x.get('weight', 0))


def _build_subject_chapter_queue(subject: str, signals: List[dict], chapters: List[dict]) -> List[dict]:
    buckets: Dict[int, dict] = {}

    for sig in signals:
        ch = None
        cnum = sig.get('chapter_num')
        if cnum:
            ch = next((c for c in chapters if c['num'] == int(cnum)), None)
        if not ch:
            ch = _match_chapter(subject, sig.get('theme') or '', chapters)
        if not ch and chapters:
            ch = chapters[0]
        if not ch:
            continue
        num = ch['num']
        if num not in buckets:
            buckets[num] = {'chapter': ch, 'signals': [], 'weight': 0}
        buckets[num]['signals'].append(sig)
        buckets[num]['weight'] += sig.get('weight', 0)

    queue = sorted(buckets.values(), key=lambda x: -x['weight'])
    covered = {b['chapter']['num'] for b in queue}
    for ch in chapters:
        if ch['num'] not in covered:
            queue.append({'chapter': ch, 'signals': [], 'weight': 0})
    return queue


def _urls_for(subject: str, chapter_num: int, quiz_category: str) -> dict:
    cours = f'/dashboard/cours/{subject}/{chapter_num}/'
    quiz = f'/dashboard/quiz/?subject={subject}'
    if quiz_category:
        from urllib.parse import quote
        quiz += f'&chapter={quote(quiz_category)}'
    exo = f'/dashboard/exercices/?subject={subject}'
    return {'cours': cours, 'quiz': quiz, 'exo': exo}


def _compose_task(label: str, chapter_title: str, chapter_num: int, subtopics: List[str], quiz_cat: str) -> str:
    head = f'Ch.{chapter_num} « {chapter_title} »'
    parts = [head]
    if subtopics:
        parts.append('Focus : ' + ', '.join(subtopics[:3]))
    if quiz_cat:
        parts.append(f'Quiz : {quiz_cat}')
    return ' · '.join(parts)


def _compose_reasons(signals: List[dict], limit: int = 3) -> str:
    details = []
    seen = set()
    for sig in signals:
        d = (sig.get('detail') or '').strip()
        if d and d not in seen:
            seen.add(d)
            details.append(d)
        if len(details) >= limit:
            break
    return ' · '.join(details)


def build_study_insights(
    user,
    mats: dict,
    get_user_serie_subjects_fn: Callable,
    limit: int = 12,
) -> List[dict]:
    """Insights structurés pour Progression (lacunes + chapitres ciblés)."""
    from core.coaching_context import build_coaching_context
    from core.learning_tracker import build_combined_weakness_scores

    ctx = build_coaching_context(user, mats, get_user_serie_subjects_fn)
    combined = build_combined_weakness_scores(user)
    today = ctx['today']
    user_subjs = list(get_user_serie_subjects_fn(user) or ctx.get('user_subjs') or [])

    ranked = sorted(
        [(s, float(combined.get(s, 50) or 50)) for s in user_subjs],
        key=lambda x: x[1],
    )

    insights: List[dict] = []
    for subj, score in ranked:
        label = mats.get(subj, {}).get('label', subj)
        chapters = _get_chapter_catalog(subj)
        signals = _collect_weakness_signals(ctx, subj, today)
        queue = _build_subject_chapter_queue(subj, signals, chapters)
        for item in queue[:2]:
            ch = item['chapter']
            sigs = item['signals']
            subtopics = _pick_subtopics(subj, ch['num'], ch['title'], sigs)
            quiz_cat = _match_quiz_category(subj, (sigs[0].get('theme') if sigs else '') or ch['title'])
            weight = item['weight']
            prio = 'high' if weight >= 8 or score < 45 else ('medium' if weight >= 4 or score < 65 else 'low')
            urls = _urls_for(subj, ch['num'], quiz_cat)
            insights.append({
                'subject': subj,
                'label': label,
                'score': int(score),
                'chapter': ch['title'],
                'chapter_num': ch['num'],
                'subtopics': subtopics,
                'reasons': [s.get('detail', '') for s in sigs[:4] if s.get('detail')],
                'weakness_reason': _compose_reasons(sigs),
                'quiz_category': quiz_cat,
                'priority': prio,
                'signal_weight': weight,
                'cours_url': urls['cours'],
                'quiz_url': urls['quiz'],
                'exo_url': urls['exo'],
            })
            if len(insights) >= limit:
                return insights
    return insights


def build_revision_plan(
    user,
    serie_key: str,
    weeks: int,
    mats: dict,
    get_user_serie_subjects_fn: Callable,
) -> Dict[str, Any]:
    from core.coaching_context import build_coaching_context
    from core.learning_tracker import build_combined_weakness_scores, get_mistake_topics_for_plan

    ctx = build_coaching_context(user, mats, get_user_serie_subjects_fn)
    combined = build_combined_weakness_scores(user)
    today = ctx['today']
    user_subjs = list(get_user_serie_subjects_fn(user) or [])
    if not user_subjs:
        from core.series_data import SERIES
        user_subjs = list(SERIES.get(serie_key, SERIES.get('SVT', {})).get('subjects', {}).keys())

    ranked: List[Tuple[str, float]] = sorted(
        [(s, float(combined.get(s, 50) or 50)) for s in user_subjs],
        key=lambda x: x[1],
    )

    due_topics = get_mistake_topics_for_plan(user) or []
    subject_queues: Dict[str, List[dict]] = {}
    subject_signals: Dict[str, List[dict]] = {}
    for subj in user_subjs:
        chapters = _get_chapter_catalog(subj)
        sigs = _collect_weakness_signals(ctx, subj, today)
        subject_signals[subj] = sigs
        subject_queues[subj] = _build_subject_chapter_queue(subj, sigs, chapters)

    # Index global pour parcourir chapitres faibles sans répéter
    day_cursor: Dict[str, int] = {s: 0 for s in user_subjs}

    def _next_study_item(subj: str, score: float) -> dict:
        label = mats.get(subj, {}).get('label', subj)
        prio = _priority(score)
        queue = subject_queues.get(subj) or []
        sigs = subject_signals.get(subj) or []
        if not queue:
            return {
                'day': '',
                'subject': label,
                'task': f'Quiz diagnostic {label} — identifier tes lacunes',
                'weakness_reason': _compose_reasons(sigs) or f'Score global {int(score)}%',
                'subtopics': [],
                'chapter': '',
                'chapter_num': 1,
                'quiz_category': '',
                'duration_min': _duration_min(prio),
                'priority': prio,
                'cours_url': f'/dashboard/cours/{subj}/1/',
                'quiz_url': f'/dashboard/quiz/?subject={subj}',
                'exo_url': f'/dashboard/exercices/?subject={subj}',
            }

        idx = day_cursor[subj] % len(queue)
        day_cursor[subj] += 1
        item = queue[idx]
        ch = item['chapter']
        item_sigs = item['signals'] or sigs[:5]
        subtopics = _pick_subtopics(subj, ch['num'], ch['title'], item_sigs)
        theme_hint = (item_sigs[0].get('theme') if item_sigs else '') or ch['title']
        quiz_cat = _match_quiz_category(subj, theme_hint)
        urls = _urls_for(subj, ch['num'], quiz_cat)
        weight = item.get('weight', 0)

        return {
            'day': '',
            'subject': label,
            'task': _compose_task(label, ch['title'], ch['num'], subtopics, quiz_cat),
            'weakness_reason': _compose_reasons(item_sigs),
            'subtopics': subtopics,
            'chapter': ch['title'],
            'chapter_num': ch['num'],
            'quiz_category': quiz_cat,
            'duration_min': _duration_min(prio, weight),
            'priority': prio,
            'cours_url': urls['cours'],
            'quiz_url': urls['quiz'],
            'exo_url': urls['exo'],
        }

    week_count = max(2, min(26, int(weeks or 8)))
    weeks_out: List[dict] = []

    for wi in range(week_count):
        focus_subj, focus_score = ranked[wi % len(ranked)]
        focus_label = mats.get(focus_subj, {}).get('label', focus_subj)
        focus_queue = subject_queues.get(focus_subj) or []
        focus_ch = focus_queue[0]['chapter']['title'] if focus_queue else ''
        focus_sigs = len(subject_signals.get(focus_subj, []))
        focus_line = f'{focus_label}'
        if focus_ch:
            focus_line += f' — {focus_ch}'
        if focus_sigs:
            focus_line += f' ({focus_sigs} signal{"aux" if focus_sigs > 1 else ""} de lacune)'

        days: List[dict] = []
        for di in range(5):
            # Alterner matières faibles : 3 jours sur focus + 2 sur autres faibles
            if di < 3:
                subj, score = ranked[wi % len(ranked)]
            else:
                alt_idx = (wi + di) % len(ranked)
                subj, score = ranked[alt_idx]
            entry = dict(_next_study_item(subj, score))
            entry['day'] = WEEKDAYS[di]
            days.append(entry)

        weeks_out.append({
            'label': 'Cette semaine' if week_count == 1 else f'Semaine {wi + 1}',
            'focus': focus_line,
            'days': days,
        })

    # Summary riche
    summary_parts = [
        'Plan construit depuis tes quiz, erreurs SM-2, cours en pause, exercices BAC et diagnostic.',
    ]
    weak_top = [mats.get(s, {}).get('label', s) for s, sc in ranked[:3]]
    if weak_top:
        summary_parts.append(f'Matières prioritaires : {", ".join(weak_top)}.')
    if due_topics:
        summary_parts.append(f'Révisions SM-2 dues : {", ".join(due_topics[:5])}.')
    active_stalled = ctx.get('courses_stalled') or []
    if active_stalled:
        stall = active_stalled[0]
        summary_parts.append(
            f'Cours à reprendre : {stall.get("title", "")} ({stall.get("days_since", 0)}j).'
        )
    total_signals = sum(len(v) for v in subject_signals.values())
    if total_signals:
        summary_parts.append(f'{total_signals} lacunes identifiées et mappées sur des chapitres réels.')

    return {
        'summary': ' '.join(summary_parts),
        'weeks': weeks_out,
        'meta': {
            'signals_total': total_signals,
            'due_sm2': len(ctx.get('mistakes_due_today') or []),
            'subjects_ranked': [{'subject': s, 'label': mats.get(s, {}).get('label', s), 'score': int(sc)} for s, sc in ranked],
        },
    }
