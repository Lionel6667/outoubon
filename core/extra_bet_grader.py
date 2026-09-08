"""
Correction Extra bèt — formats structurés uniquement :
  word   — un seul mot (synonymes avec |)
  qcm    — choix multiple
  match  — relier colonnes (JSON index → index)
  parts  — sous-questions a) b) c) — valeurs exactes
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

FRENCH_ARTICLES = frozenset({'le', 'la', 'les', 'un', 'une', 'des', 'du', 'de', 'd', 'l', 'au', 'aux'})

ALLOWED_TYPES = frozenset({'word', 'qcm', 'match', 'parts', 'direct', 'fill'})


def normalize_text(text: str) -> str:
    text = (text or '').strip().lower()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    text = text.replace(',', '.')
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^a-z0-9\s.\-+]', '', text)
    return text.strip()


def normalize_exact_value(text: str) -> str:
    """Valeur exacte a) b) c) — nombre ou texte court normalisé."""
    raw = (text or '').strip()
    if not raw:
        return ''
    cleaned = raw.replace(',', '.').replace(' ', '')
    try:
        num = float(cleaned)
        if num == int(num):
            return str(int(num))
        return str(round(num, 6)).rstrip('0').rstrip('.')
    except ValueError:
        return normalize_text(raw).replace(' ', '')


def _levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def _edit_distance_ok(a: str, b: str) -> bool:
    if a == b:
        return True
    dist = _levenshtein_distance(a, b)
    max_len = max(len(a), len(b))
    if dist <= 1:
        return True
    if max_len and dist / max_len <= 0.15:
        return True
    return False


def parse_acceptable_words(expected_raw: str) -> List[str]:
    parts = re.split(r'[|;]', (expected_raw or '').strip())
    out: List[str] = []
    for part in parts:
        w = normalize_text(part).replace(' ', '')
        if w and w not in out:
            out.append(w)
    return out


def is_single_word(text: str) -> bool:
    t = (text or '').strip()
    if not t:
        return False
  # un mot : pas d'espace (tiret ok)
    return ' ' not in t and '\t' not in t


def grade_word(submitted: str, expected_raw: str) -> Tuple[bool, str]:
    display = (expected_raw or '').split('|')[0].strip()
    if not is_single_word(submitted):
        return False, display
    sub = normalize_text(submitted).replace(' ', '')
    if not sub:
        return False, display
    acceptable = parse_acceptable_words(expected_raw)
    if not acceptable:
        return False, display
    for exp in acceptable:
        if sub == exp or _edit_distance_ok(sub, exp):
            return True, display
    return False, display


def grade_qcm(submitted: str, expected_raw: str, options: list) -> Tuple[bool, str]:
    s_norm = normalize_text(submitted)
    e_norm = normalize_text(expected_raw)
    opts = [str(o).strip() for o in (options or [])]
    display = expected_raw.strip().upper() if e_norm in 'abcd' else display_word(expected_raw)

    if len(s_norm) == 1 and s_norm in 'abcd':
        if len(e_norm) == 1 and e_norm in 'abcd':
            return s_norm == e_norm, display
        idx = ord(s_norm) - ord('a')
        if 0 <= idx < len(opts):
            return s_norm == e_norm, display
    if len(e_norm) == 1 and e_norm in 'abcd':
        idx = ord(e_norm) - ord('a')
        if 0 <= idx < len(opts):
            return s_norm == normalize_text(opts[idx]), display
    return s_norm == e_norm, display


def display_word(s: str) -> str:
    return (s or '').split('|')[0].strip()


def _parse_json_map(raw: str) -> Dict[str, str]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def format_match_display(mapping: Dict[str, str], options: dict) -> str:
    left = options.get('left') or []
    right = options.get('right') or []
    bits = []
    for i, ltxt in enumerate(left):
        ri = mapping.get(str(i), mapping.get(i, ''))
        try:
            rtxt = right[int(ri)] if ri != '' else '?'
        except (ValueError, IndexError):
            rtxt = '?'
        bits.append(f'{ltxt} → {rtxt}')
    return ' · '.join(bits) if bits else json.dumps(mapping)


def grade_match(submitted: str, expected_raw: str, options: Any) -> Tuple[bool, str]:
    opts = options if isinstance(options, dict) else {}
    left = opts.get('left') or []
    exp_map = _parse_json_map(expected_raw)
    sub_map = _parse_json_map(submitted)
    display = format_match_display(exp_map, opts)
    if len(left) != len(exp_map):
        return False, display
    for i in range(len(left)):
        if sub_map.get(str(i)) != exp_map.get(str(i)):
            return False, display
    return True, display


def grade_parts(submitted: str, expected_raw: str, options: Any) -> Tuple[bool, str]:
    opts = options if isinstance(options, dict) else {}
    parts = opts.get('parts') or []
    exp_map = _parse_json_map(expected_raw)
    sub_map = _parse_json_map(submitted)
    display_bits = []
    for p in parts:
        pid = str(p.get('id', ''))
        display_bits.append(f'{p.get("label", pid)} = {exp_map.get(pid, "?")}')
    display = ' · '.join(display_bits)

    for p in parts:
        pid = str(p.get('id', ''))
        if pid not in exp_map:
            continue
        sub_val = normalize_exact_value(sub_map.get(pid, ''))
        exp_val = normalize_exact_value(exp_map.get(pid, ''))
        if not sub_val or sub_val != exp_val:
            return False, display
    if not parts:
        return False, display
    return True, display


def grade_extra_bet_answer(
    submitted: str,
    expected_raw: str,
    question_type: str,
    options: Optional[Any] = None,
) -> Tuple[bool, str]:
    qtype = (question_type or 'word').lower()
    if qtype in ('direct', 'fill'):
        qtype = 'word'

    if qtype == 'qcm':
        opt_list = options if isinstance(options, list) else []
        return grade_qcm(submitted, expected_raw, opt_list)

    if qtype == 'match':
        return grade_match(submitted, expected_raw, options or {})

    if qtype == 'parts':
        return grade_parts(submitted, expected_raw, options or {})

    return grade_word(submitted, expected_raw)


def verify_extra_bet_submission(
    question_type: str,
    prompt: str,
    answer: str,
    options: Any,
) -> dict:
    qtype = (question_type or '').lower()
    prompt = (prompt or '').strip()
    answer = (answer or '').strip()

    if qtype in ('direct', 'fill'):
        qtype = 'word'

    if len(prompt) < 8:
        return {'valid': False, 'reason': 'L\'énoncé doit faire au moins 8 caractères.', 'correct_answer': answer}

    if qtype == 'word':
        variants = [v.strip() for v in re.split(r'[|;]', answer) if v.strip()]
        if not variants:
            return {'valid': False, 'reason': 'Indique le mot correct.', 'correct_answer': answer}
        for v in variants:
            if not is_single_word(v):
                return {
                    'valid': False,
                    'reason': f'« {v} » n\'est pas un seul mot. Utilise le type Exercice a) b) c) pour plusieurs valeurs.',
                    'correct_answer': answer,
                }
        if normalize_text(prompt) == normalize_text(variants[0]):
            return {'valid': False, 'reason': 'La réponse ne peut pas être identique à la question.', 'correct_answer': answer}

    elif qtype == 'qcm':
        opt_list = options if isinstance(options, list) else []
        letter = normalize_text(answer)
        if len(letter) != 1 or letter not in 'abcd':
            return {'valid': False, 'reason': 'Bonne réponse QCM : A, B, C ou D.', 'correct_answer': answer}
        if len(opt_list) < 2:
            return {'valid': False, 'reason': 'Au moins 2 options QCM.', 'correct_answer': answer}
        idx = ord(letter) - ord('a')
        if idx >= len(opt_list):
            return {'valid': False, 'reason': f'Option {answer.upper()} manquante.', 'correct_answer': answer}

    elif qtype == 'match':
        opts = options if isinstance(options, dict) else {}
        left = opts.get('left') or []
        right = opts.get('right') or []
        if len(left) < 2 or len(right) < 2:
            return {'valid': False, 'reason': 'Au moins 2 éléments dans chaque colonne.', 'correct_answer': answer}
        exp_map = _parse_json_map(answer)
        if len(exp_map) != len(left):
            return {'valid': False, 'reason': 'Définis une liaison pour chaque élément de gauche.', 'correct_answer': answer}
        for i in range(len(left)):
            ri = exp_map.get(str(i))
            if ri is None or int(ri) >= len(right):
                return {'valid': False, 'reason': f'Liaison invalide pour l\'élément {i + 1}.', 'correct_answer': answer}

    elif qtype == 'parts':
        opts = options if isinstance(options, dict) else {}
        parts = opts.get('parts') or []
        exp_map = _parse_json_map(answer)
        if len(parts) < 1:
            return {'valid': False, 'reason': 'Ajoute au moins une sous-question (a, b, …).', 'correct_answer': answer}
        for p in parts:
            pid = str(p.get('id', ''))
            if pid not in exp_map or not str(exp_map[pid]).strip():
                return {'valid': False, 'reason': f'Valeur exacte manquante pour {p.get("label", pid)}.', 'correct_answer': answer}

    else:
        return {'valid': False, 'reason': 'Type de question invalide.', 'correct_answer': answer}

    return {
        'valid': True,
        'reason': 'Publication validée.',
        'correct_answer': answer,
    }
