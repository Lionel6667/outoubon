"""
Moteur de tutorat guidé pour les exercices — logique type Astra AI.

Session structurée : une question à la fois, évaluation, indices progressifs,
avancement automatique, note finale.
"""

from __future__ import annotations

import json
import re
from typing import Any


def normalize_questions(exercise: dict) -> list[str]:
    raw = exercise.get('questions') or []
    out = [str(q).strip() for q in raw if str(q).strip()]
    if out:
        return out
    intro = (exercise.get('intro') or exercise.get('enonce') or '').strip()
    if intro:
        return ['Résous cet exercice en expliquant ta démarche étape par étape.']
    return ['Explique ta démarche pour résoudre cet exercice.']


def init_session(exercise: dict) -> dict:
    qs = normalize_questions(exercise)
    n = len(qs)
    return {
        'current_index': 0,
        'total': n,
        'statuses': ['pending'] * n,
        'hints_used': [0] * n,
        'attempts': [0] * n,
        'phase': 'intro',
        'completed': False,
        'score_estimate': None,
    }


def _clamp_index(session: dict, idx: int) -> int:
    total = max(1, int(session.get('total') or 1))
    return max(0, min(idx, total - 1))


def session_public_view(session: dict, exercise: dict) -> dict:
    qs = normalize_questions(exercise)
    idx = _clamp_index(session, int(session.get('current_index') or 0))
    total = int(session.get('total') or len(qs) or 1)
    statuses = list(session.get('statuses') or [])
    done = sum(1 for s in statuses if s in ('correct', 'partial', 'skipped'))
    in_progress = (
        0.35
        if not session.get('completed')
        and idx < len(statuses)
        and statuses[idx] in ('in_progress', 'pending', 'wrong', 'partial', 'started')
        else 0.0
    )
    return {
        'current_index': idx,
        'total': total,
        'current_label': qs[idx] if idx < len(qs) else '',
        'statuses': list(session.get('statuses') or []),
        'hints_used': list(session.get('hints_used') or []),
        'phase': session.get('phase') or 'active',
        'completed': bool(session.get('completed')),
        'progress_pct': round((done + in_progress) / total * 100) if total else 0,
        'done_count': done,
        'score_estimate': session.get('score_estimate'),
    }


def detect_message_mode(message: str) -> str:
    m = (message or '').strip()
    if m.startswith('[INTRO]'):
        return 'intro'
    if m.startswith('[HINT]'):
        return 'hint'
    if m.startswith('[FINISH]'):
        return 'finish'
    if m.startswith('[SKIP]'):
        return 'skip'
    if m.startswith('[METHOD]'):
        return 'method'
    return 'answer'


def apply_hint(session: dict) -> dict:
    session = dict(session)
    idx = _clamp_index(session, int(session.get('current_index') or 0))
    hints = list(session.get('hints_used') or [0] * session.get('total', 1))
    while len(hints) < session.get('total', 1):
        hints.append(0)
    hints[idx] = int(hints[idx] or 0) + 1
    session['hints_used'] = hints
    statuses = list(session.get('statuses') or ['pending'] * session.get('total', 1))
    if statuses[idx] == 'pending':
        statuses[idx] = 'in_progress'
    session['statuses'] = statuses
    session['phase'] = 'active'
    return session


def advance_question(session: dict, evaluation: str = 'partial') -> dict:
    session = dict(session)
    idx = _clamp_index(session, int(session.get('current_index') or 0))
    total = int(session.get('total') or 1)
    statuses = list(session.get('statuses') or ['pending'] * total)
    while len(statuses) < total:
        statuses.append('pending')
    if evaluation in ('correct', 'partial', 'wrong', 'skipped'):
        statuses[idx] = evaluation if evaluation != 'skipped' else 'skipped'
    session['statuses'] = statuses

    if idx + 1 < total:
        session['current_index'] = idx + 1
        if statuses[idx + 1] == 'pending':
            statuses[idx + 1] = 'in_progress'
        session['statuses'] = statuses
        session['phase'] = 'active'
        session['completed'] = False
    else:
        session['phase'] = 'review'
        session['completed'] = True
    return session


def parse_ai_directives(response: str, session: dict) -> tuple[str, dict, dict]:
    """Extrait balises [EVAL:...], [ADVANCE], [NOTE:X/10] et nettoie la réponse."""
    session = dict(session)
    meta = {'evaluation': None, 'advance': False, 'note': None}
    text = response or ''

    note_m = re.search(r'\[NOTE:(\d+(?:\.\d+)?)/10\]', text, re.I)
    if note_m:
        meta['note'] = float(note_m.group(1))
        session['score_estimate'] = meta['note']
        session['completed'] = True
        session['phase'] = 'done'

    eval_m = re.search(r'\[EVAL:(correct|partial|wrong|off_topic)\]', text, re.I)
    if eval_m:
        meta['evaluation'] = eval_m.group(1).lower()

    if re.search(r'\[ADVANCE\]', text, re.I):
        meta['advance'] = True

    text = re.sub(r'\[EVAL:(correct|partial|wrong|off_topic)\]', '', text, flags=re.I)
    text = re.sub(r'\[ADVANCE\]', '', text, flags=re.I)
    text = re.sub(r'\[NOTE:\d+(?:\.\d+)?/10\]', '', text, flags=re.I)
    text = re.sub(r'\s{2,}', ' ', text).strip()

    if meta['advance'] and meta['evaluation']:
        session = advance_question(session, meta['evaluation'])
    elif meta['evaluation'] == 'correct' and not session.get('completed'):
        idx = _clamp_index(session, int(session.get('current_index') or 0))
        statuses = list(session.get('statuses') or [])
        if idx < len(statuses):
            statuses[idx] = 'correct'
            session['statuses'] = statuses

    return text, session, meta


def build_exercise_context(exercise: dict, subject: str, current_index: int | None = None) -> str:
    intro = exercise.get('intro') or exercise.get('enonce', '')
    questions = normalize_questions(exercise)
    texte = exercise.get('texte', '')
    parts = [f"Énoncé:\n{intro[:1800]}"]
    if texte:
        parts.append(f"Texte:\n{texte[:500]}")
    total = len(questions)
    if current_index is not None and 0 <= current_index < total:
        parts.append(f"Question active ({current_index + 1}/{total}):\n{questions[current_index]}")
    elif questions:
        parts.append('Questions:\n' + '\n'.join(f"  {i+1}. {q}" for i, q in enumerate(questions)))

    reponses = exercise.get('reponses')
    secret_bits: list[str] = []
    if reponses:
        if isinstance(reponses, dict):
            if current_index is not None:
                keys = [chr(ord('a') + current_index), str(current_index + 1), f'q{current_index + 1}']
                for k in keys:
                    if reponses.get(k):
                        secret_bits.append(f"  {k}) {reponses[k]}")
                        break
            if not secret_bits:
                secret_bits = [f"  {k}) {v}" for k, v in list(reponses.items())[:4] if v]
        elif isinstance(reponses, list):
            if current_index is not None and current_index < len(reponses) and reponses[current_index]:
                secret_bits = [f"  - {reponses[current_index]}"]
            else:
                secret_bits = [f"  - {r}" for r in reponses[:4] if r]
        elif reponses:
            secret_bits = [str(reponses)[:400]]
    if secret_bits:
        parts.append(
            "Réponse officielle de la question active (SECRET — guider, ne jamais coller tel quel):\n"
            + '\n'.join(secret_bits)[:700]
        )
    sol = exercise.get('solution', '')
    if sol and current_index is None:
        parts.append(f"Solution (secrète): {sol[:400]}")
    return '\n\n'.join(parts)


def build_system_prompt(
    exercise: dict,
    subject: str,
    student_name: str,
    session: dict,
    mode: str,
    user_lang_rule: str = '',
    student_profile: str = '',
    mastery_level: str = '',
    mastery_score: int = 0,
) -> str:
    questions = normalize_questions(exercise)
    total = len(questions)
    idx = _clamp_index(session, int(session.get('current_index') or 0))
    current_q = questions[idx] if idx < len(questions) else ''
    ctx = build_exercise_context(
        exercise, subject, current_index=None if mode == 'intro' else idx,
    )
    hints_used = (session.get('hints_used') or [0] * total)[idx] if idx < total else 0
    statuses = session.get('statuses') or []

    level_block = ''
    if mastery_level or mastery_score:
        level_block = f"NIVEAU ÉLÈVE : {mastery_level or 'intermediaire'} (maîtrise ~{mastery_score or 50}%).\n"
        if mastery_level in ('debutant', 'apprenti') or mastery_score < 40:
            level_block += (
                "Adapte-toi : vocabulaire simple, petites étapes, une formule à la fois, "
                "vérifie la compréhension avant d'avancer.\n"
            )
        elif mastery_level in ('avance', 'expert') or mastery_score >= 75:
            level_block += "Adapte-toi : élève solide — sois plus concis, pose des questions de synthèse.\n"

    profile_block = ''
    if student_profile and student_profile.strip():
        profile_block = f"PROFIL : {student_profile[:350]}\n"

    base_rules = (
        f"Tu es Prof Bac — tuteur expert du BAC Haïti pour {student_name}.\n"
        f"{user_lang_rule}"
        f"{level_block}{profile_block}"
        f"STYLE : clair, encourageant, jamais condescendant. Phrases courtes. Une idée à la fois.\n"
        f"Tu expliques l'exercice comme un bon prof : l'élève essaie de résoudre, tu l'accompagnes — ce n'est pas un QCM ni un test.\n"
        f"RÈGLE D'OR : ne donne JAMAIS la réponse finale directement — guide par questions.\n\n"
        f"--- EXERCICE ---\n{ctx}\n--- FIN ---\n\n"
        f"PROGRESSION : question {idx + 1}/{total}.\n"
        f"Question active : {current_q}\n"
        f"Statuts : {statuses}\n"
    )

    tag_rules = (
        "BALISES OBLIGATOIRES (en fin de message, sur leur propre ligne) :\n"
        "• [EVAL:correct] si la réponse est juste ou suffisamment complète\n"
        "• [EVAL:partial] si bonne idée mais incomplet\n"
        "• [EVAL:wrong] si incorrect — corrige puis re-guide\n"
        "• [EVAL:off_topic] si hors sujet\n"
        "• [ADVANCE] si tu passes à la question suivante (avec correct ou partial)\n"
        "• [NOTE:X/10] UNIQUEMENT quand TOUTES les questions sont traitées ou sur demande FINISH\n"
    )

    if mode == 'intro':
        return (
            base_rules
            + "MODE : INTRODUCTION.\n"
            + "1. Accueille brièvement l'élève.\n"
            + "2. Résume en 2 phrases ce que l'exercice demande.\n"
            + "3. Annonce le nombre de questions (" + str(total) + ").\n"
            + "4. Pose la première question de façon socratique — demande son approche.\n"
            + "5. Pas de balise [ADVANCE] ni [NOTE] ici.\n"
        )

    if mode == 'hint':
        level = min(3, int(hints_used) + 1)
        return (
            base_rules
            + f"MODE : INDICE niveau {level}/3.\n"
            + "Niveau 1 : rappelle la formule/loi clé en LaTeX.\n"
            + "Niveau 2 : explique quelle étape faire sans calculer.\n"
            + "Niveau 3 : méthode détaillée sans donner le résultat numérique final.\n"
            + "Commence par 'Indice :' — 3-4 phrases max. Pas de [ADVANCE].\n"
        )

    if mode == 'method':
        return (
            base_rules
            + "MODE : MÉTHODE.\n"
            + "Explique la méthode générale pour CE type de question (étapes numérotées).\n"
            + "Ne donne pas le résultat final. Pas de [ADVANCE].\n"
        )

    if mode == 'skip':
        return (
            base_rules
            + "MODE : PASSER.\n"
            + "L'élève veut passer cette question. Donne un résumé court de la bonne approche (2-3 phrases),\n"
            + "puis annonce la question suivante ou conclus si c'était la dernière.\n"
            + "Termine par [EVAL:skipped] et [ADVANCE] si ce n'est pas la dernière question.\n"
        )

    if mode == 'finish':
        return (
            base_rules
            + "MODE : BILAN FINAL.\n"
            + "Évalue objectivement la session (proportions de réponses justes).\n"
            + "2-3 phrases de feedback. Termine OBLIGATOIREMENT par [NOTE:X/10].\n"
            + "Sois strict mais encourageant.\n"
        )

    # mode == 'answer'
    return (
        base_rules
        + tag_rules
        + "MODE : RÉPONSE ÉLÈVE.\n"
        + "1. Évalue ce que l'élève vient d'écrire.\n"
        + "2. Si correct → félicite brièvement, [EVAL:correct] [ADVANCE], pose la question suivante.\n"
        + "3. Si partiel → complète ce qui manque, [EVAL:partial], guide vers la suite.\n"
        + "4. Si faux → corrige avec bienveillance, [EVAL:wrong], repose une sous-question.\n"
        + "5. Maximum 4-5 phrases + balises.\n"
    )


def compute_session_summary(session: dict, exercise: dict) -> dict:
    """Bilan local : notions maîtrisées vs à revoir (sans appel IA)."""
    qs = normalize_questions(exercise)
    statuses = list(session.get('statuses') or [])
    mastered, missed = [], []
    for i, q in enumerate(qs):
        st = statuses[i] if i < len(statuses) else 'pending'
        label = (q[:80] + '…') if len(q) > 80 else q
        if st in ('correct',):
            mastered.append(label)
        elif st in ('wrong', 'partial', 'skipped', 'pending'):
            if st != 'pending' or session.get('completed'):
                missed.append(label)
    correct_n = sum(1 for s in statuses if s == 'correct')
    total = max(1, len(qs))
    local_score = round(correct_n / total * 10, 1)
    note = session.get('score_estimate')
    if note is None:
        partial_n = sum(1 for s in statuses if s == 'partial')
        note = round((correct_n + partial_n * 0.5) / total * 10, 1)
    theme = exercise.get('theme') or exercise.get('chapter') or ''
    return {
        'note': float(note) if note is not None else local_score,
        'mastered': mastered,
        'missed': missed,
        'correct_count': correct_n,
        'total': total,
        'theme': theme,
    }


def get_progressive_hint(exercise: dict, q_index: int, level: int) -> str | None:
    """
    Indices locaux (0 appel IA) depuis reponses[] officielles.
    level 1-3 : de plus en plus explicite, sans donner la réponse complète.
    """
    reponses = exercise.get('reponses') or {}
    if not reponses:
        return None
    keys = [chr(ord('a') + q_index), str(q_index + 1), f'q{q_index + 1}']
    official = ''
    for k in keys:
        if k in reponses and reponses[k]:
            official = str(reponses[k]).strip()
            break
    if not official and isinstance(reponses, list) and q_index < len(reponses):
        official = str(reponses[q_index]).strip()
    if not official:
        return None
    theme = exercise.get('theme') or exercise.get('chapter') or 'ce chapitre'
    if level <= 1:
        return f"💡 Niveau 1 — Quelle loi ou définition du thème « {theme[:40]} » s'applique ici ?"
    if level == 2:
        snippet = official.split('.')[0][:100]
        return f"💡 Niveau 2 — {snippet}{'…' if len(official) > 100 else ''}"
    snippet = official[:160]
    return f"💡 Niveau 3 — Piste : {snippet}{'…' if len(official) > 160 else ''}"


def should_offer_tutor(session: dict) -> bool:
    """Propose le tuteur après 2 échecs sur la question courante."""
    idx = _clamp_index(session, int(session.get('current_index') or 0))
    attempts = list(session.get('attempts') or [])
    if idx < len(attempts) and int(attempts[idx] or 0) >= 2:
        statuses = list(session.get('statuses') or [])
        if idx < len(statuses) and statuses[idx] == 'wrong':
            return True
    return False


def build_tutor_prompt_short(
    exercise: dict,
    subject: str,
    student_name: str,
    session: dict,
    user_lang_rule: str = '',
) -> str:
    """Prompt ultra-court pour le tiroir tuteur (2-3 phrases max, guide sans révéler)."""
    questions = normalize_questions(exercise)
    idx = _clamp_index(session, int(session.get('current_index') or 0))
    current_q = questions[idx] if idx < len(questions) else ''
    ctx = build_exercise_context(exercise, subject, current_index=idx)
    return (
        f"Tuteur BAC Haïti — {student_name}. {user_lang_rule}"
        f"Réponds en 2-3 phrases MAX. Guide sans donner la réponse finale.\n"
        f"Utilise les réponses officielles secrètes pour orienter.\n\n"
        f"{ctx}\n\n"
        f"Question active ({idx + 1}/{len(questions)}) : {current_q}\n"
        f"Pas de balises [EVAL] ni [ADVANCE]. Pas de [NOTE].\n"
    )


def opening_message(exercise: dict, student_name: str, coach_name: str = '') -> str:
    qs = normalize_questions(exercise)
    theme = exercise.get('theme') or exercise.get('matiere') or 'Exercice'
    n = len(qs)
    coach = (coach_name or '').strip()
    coach_line = f"**{coach}**" if coach else "Ton tuteur"
    parts = [
        f"Salut {student_name} ! {coach_line} t'accompagne sur **{theme}**.",
        "",
        "L'énoncé est juste au-dessus — lis-le tranquillement. "
        "On travaille **question par question**, sans donner la réponse toute faite.",
    ]
    if n > 1:
        parts.append(f"\nIl y a **{n} questions** (a, b, c…). On commence par la **première**.")
    elif n == 1:
        parts.append("\nOn commence par la **première partie**.")
    parts.append("\n**Quelle est ton approche ?**")
    return "\n".join(parts)
