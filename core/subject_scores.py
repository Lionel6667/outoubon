"""
Scores par matière — source unique pour tout le site.
Quiz + exercices (SM-2) + bonus cours → score blended 0–100.
"""
from __future__ import annotations

from typing import Dict, Optional, Set

NO_EXERCISE_SUBJECTS = frozenset({'francais', 'histoire', 'informatique', 'art'})


def _mat_keys():
    from core.views import MATS
    return MATS


def compute_all_blended_scores(user) -> dict:
    from core.models import CourseSession, MistakeTracker, QuizSession

    mats = _mat_keys()
    all_sessions = QuizSession.objects.filter(user=user).only('subject', 'score', 'total')
    all_mistakes = MistakeTracker.objects.filter(user=user).only('subject', 'correct_streak')
    all_courses = set(CourseSession.objects.filter(user=user).values_list('chapter_subject', flat=True))

    subjects_data = {
        s: {'pcts': [], 'm_total': 0, 'm_recov': 0, 'has_course': False}
        for s in mats
    }

    for s in all_sessions:
        if s.subject in subjects_data and s.total > 0:
            subjects_data[s.subject]['pcts'].append(round((s.score / s.total) * 100))

    for m in all_mistakes:
        if m.subject in subjects_data:
            subjects_data[m.subject]['m_total'] += 1
            if m.correct_streak > 0:
                subjects_data[m.subject]['m_recov'] += 1

    for subj in all_courses:
        if subj in subjects_data:
            subjects_data[subj]['has_course'] = True

    results = {}
    for subj, data in subjects_data.items():
        quiz_avg = round(sum(data['pcts']) / len(data['pcts'])) if data['pcts'] else None
        exo_pct = None
        if subj not in NO_EXERCISE_SUBJECTS and data['m_total'] > 0:
            exo_pct = round((data['m_recov'] / data['m_total']) * 100)
        has_course = data['has_course']
        course_bonus = 20 if has_course else 0

        blended = None
        if subj in NO_EXERCISE_SUBJECTS:
            if quiz_avg is not None:
                blended = round(quiz_avg * 0.80 + course_bonus)
            else:
                blended = course_bonus if has_course else None
        else:
            if quiz_avg is not None and exo_pct is not None:
                blended = round(quiz_avg * 0.30 + exo_pct * 0.50 + course_bonus)
            elif quiz_avg is not None:
                blended = round(quiz_avg * 0.50 + course_bonus)
            elif exo_pct is not None:
                blended = round(exo_pct * 0.80 + course_bonus)
            else:
                blended = course_bonus if has_course else None

        if blended is not None:
            blended = min(100, blended)

        results[subj] = {
            'blended': blended,
            'quiz_avg': quiz_avg,
            'exo_pct': exo_pct,
            'course_bonus': course_bonus,
            'has_course': has_course,
            'quiz_count': len(data['pcts']),
            'exo_total': data['m_total'],
        }
    return results


def compute_subject_blended_score(user, subj: str) -> dict:
    from core.models import CourseSession, MistakeTracker, QuizSession

    sessions = QuizSession.objects.filter(user=user, subject=subj)
    pcts = [round((s.score / s.total) * 100) for s in sessions if s.total and s.total > 0]
    quiz_avg = round(sum(pcts) / len(pcts)) if pcts else None
    quiz_count = len(pcts)

    exo_pct = None
    exo_total = 0
    if subj not in NO_EXERCISE_SUBJECTS:
        mistakes = MistakeTracker.objects.filter(user=user, subject=subj)
        exo_total = mistakes.count()
        if exo_total:
            recovering = mistakes.filter(correct_streak__gt=0).count()
            exo_pct = round((recovering / exo_total) * 100)

    has_course = CourseSession.objects.filter(user=user, chapter_subject=subj).exists()
    course_bonus = 20 if has_course else 0

    if subj in NO_EXERCISE_SUBJECTS:
        if quiz_avg is not None:
            blended = round(quiz_avg * 0.80 + course_bonus)
        else:
            blended = course_bonus if has_course else None
    else:
        if quiz_avg is not None and exo_pct is not None:
            blended = round(quiz_avg * 0.30 + exo_pct * 0.50 + course_bonus)
        elif quiz_avg is not None:
            blended = round(quiz_avg * 0.50 + course_bonus)
        elif exo_pct is not None:
            blended = round(exo_pct * 0.80 + course_bonus)
        else:
            blended = course_bonus if has_course else None

    if blended is not None:
        blended = min(100, blended)

    return {
        'quiz_avg': quiz_avg,
        'quiz_count': quiz_count,
        'exo_pct': exo_pct,
        'exo_total': exo_total,
        'has_course': has_course,
        'course_bonus': course_bonus,
        'blended': blended,
    }


def get_scores_for_user(
    user,
    serie_subjects: Set[str],
    diag_scores: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """Score affiché 0–100 par matière de la série — identique partout."""
    if diag_scores is None:
        from accounts.models import DiagnosticResult
        diag_scores = {
            d.subject: int(d.score)
            for d in DiagnosticResult.objects.filter(user=user)
        }
    all_blended = compute_all_blended_scores(user)
    out: Dict[str, int] = {}
    for subj in serie_subjects:
        sc = all_blended.get(subj, {})
        if sc.get('blended') is not None:
            out[subj] = int(sc['blended'])
        elif subj in diag_scores:
            out[subj] = int(diag_scores[subj])
        else:
            out[subj] = 0
    return out


def estimate_bac_score(scores: Dict[str, int], serie_key: str, series_config: dict) -> int:
    """Note BAC estimée /1900 selon coefficients officiels."""
    try:
        coeffs = series_config.get(serie_key, series_config.get('SVT', {}))['subjects']
        weighted = 0.0
        total_coeff = sum(coeffs.values())
        for subj, coef in coeffs.items():
            if subj in scores:
                weighted += (scores[subj] / 100.0) * coef
        if total_coeff > 0:
            return round((weighted / total_coeff) * 1900)
    except Exception:
        pass
    if scores:
        avg = sum(scores.values()) / len(scores)
        return round(avg / 100 * 1900)
    return 0
