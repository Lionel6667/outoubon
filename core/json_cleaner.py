"""
Nettoyage local des JSON pédagogiques (sans appel API).

- chapters_*.json : texte OCR dupliqué → champs structurés propres
- exams_*.json    : OCR léger + parts regex retirées si items rebuild
- exo_*.json      : correction encodage mojibake
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

# Taille max du champ text par chapitre (évite les dumps OCR monstrueux)
CHAPTER_TEXT_MAX = 6_000
EXAM_TEXT_MAX = 12_000

# Consignes d'examen à exclure des parts regex
_EXAM_CONSIGNE_PATTERNS = re.compile(
    r"(?i)(calculatrice|téléphone|telephone|silence est obligatoire|"
    r"durée de l.épreuve|coefficient|consignes?\s*:)",
)

_CONSIGNE_NUM_RE = re.compile(r"^\d+\s*[-.)]\s*", re.M)


def fix_mojibake(text: str) -> str:
    """Corrige le double-encodage UTF-8 → Latin-1 courant dans les exports."""
    if not text or not isinstance(text, str):
        return text or ''
    # Déjà correct si peu de séquences suspectes
    if text.count('Ã') < 2 and text.count('â') < 5:
        return text
    for _ in range(2):
        try:
            fixed = text.encode('latin-1', errors='ignore').decode('utf-8', errors='ignore')
            if fixed and len(fixed) >= len(text) * 0.85:
                text = fixed
            else:
                break
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
    return text


def clean_ocr_text(text: str) -> str:
    """Nettoie artefacts OCR courants (programmes + examens)."""
    if not text:
        return ''
    text = fix_mojibake(text)
    text = text.replace('\ufffd', 'é').replace('\u2190', '←')
    text = re.sub(r'(\w)0on\b', r'\1tion', text)
    text = re.sub(r'(\w)0ons\b', r'\1tions', text)
    text = re.sub(r'\b0on\b', 'tion', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{4,}', '\n\n', text)
    text = re.sub(r' {3,}', '  ', text)
    # Mots PDF inversés (ex. emhtiroglA)
    def _fix_reversed_word(m: re.Match) -> str:
        w = m.group(0)
        if len(w) < 5:
            return w
        rev = w[::-1]
        if rev[0].isupper() or rev.lower() in (
            'algorithme', 'variables', 'boucles', 'conditions',
        ):
            return rev
        return w

    text = re.sub(r'\b[A-Za-zÀ-ÿ]{6,}\b', _fix_reversed_word, text)
    return text.strip()


def fix_title(title: str) -> str:
    t = clean_ocr_text(fix_mojibake(str(title or '').strip()))
    t = re.sub(r'\s+', ' ', t)
    # Apostrophes manquantes courantes
    t = re.sub(r"\bl\s*algorithme\b", "l'algorithme", t, flags=re.I)
    t = re.sub(r"\bl\s*execution\b", "l'exécution", t, flags=re.I)
    return t.strip() or 'Chapitre'


def _text_hash(text: str) -> str:
    return hashlib.md5((text or '').encode('utf-8', errors='ignore')).hexdigest()


def synthesize_chapter_text(ch: dict) -> str:
    """Construit un texte propre à partir des champs structurés."""
    parts: list[str] = []
    summary = (ch.get('summary') or '').strip()
    if summary:
        parts.append(summary)
    competences = [str(x).strip() for x in (ch.get('competences') or []) if str(x).strip()]
    if competences:
        parts.append('Compétences visées :\n' + '\n'.join(f'• {c}' for c in competences))
    contenus = [str(x).strip() for x in (ch.get('contenus') or []) if str(x).strip()]
    if contenus:
        parts.append('Contenus du programme :\n' + '\n'.join(f'• {c}' for c in contenus))
    definitions = ch.get('definitions') or []
    if definitions:
        lines = []
        for d in definitions:
            if isinstance(d, dict) and d.get('term'):
                lines.append(f"• {d['term']} : {d.get('def', '')}")
        if lines:
            parts.append('Définitions clés :\n' + '\n'.join(lines))
    if not parts:
        return ''
    return clean_ocr_text('\n\n'.join(parts))


def _split_text_by_titles(full_text: str, chapters: list[dict]) -> dict[int, str]:
    """Découpe un blob OCR partagé par titres de chapitres."""
    if not full_text or len(chapters) < 2:
        return {}
    text = full_text
    markers: list[tuple[int, str]] = []
    for ch in chapters:
        title = fix_title(ch.get('title', ''))
        if len(title) < 4:
            continue
        # Cherche titre ou mots-clés (3+ mots significatifs)
        words = [w for w in re.findall(r'[A-Za-zÀ-ÿ]{4,}', title) if w.lower() not in ('chapitre', 'module')]
        if not words:
            continue
        pattern = '|'.join(re.escape(w) for w in words[:3])
        m = re.search(pattern, text, re.I)
        if m:
            markers.append((m.start(), title))
    markers.sort(key=lambda x: x[0])
    if len(markers) < 2:
        return {}
    result: dict[int, str] = {}
    for i, (pos, _) in enumerate(markers):
        num = chapters[i].get('num', i + 1) if i < len(chapters) else i + 1
        end = markers[i + 1][0] if i + 1 < len(markers) else len(text)
        chunk = clean_ocr_text(text[pos:end])
        if chunk:
            result[num] = chunk[:CHAPTER_TEXT_MAX]
    return result


def _estimate_pages(total_pages: list, index: int, total_chapters: int) -> list:
    """Répartit les pages PDF entre chapitres quand tout le monde a la même liste."""
    if not total_pages or total_chapters < 1:
        return []
    pages = sorted(set(int(p) for p in total_pages if isinstance(p, (int, float))))
    if not pages:
        return []
    n = len(pages)
    per = max(1, n // total_chapters)
    start = index * per
    end = (index + 1) * per if index < total_chapters - 1 else n
    return pages[start:end]


def sanitize_chapter(
    ch: dict,
    *,
    index: int = 0,
    total: int = 1,
    shared_text: str = '',
    duplicate_blob: bool = False,
    split_map: dict | None = None,
) -> dict:
    """Normalise un chapitre pour usage runtime."""
    out = dict(ch)
    out['title'] = fix_title(out.get('title', ''))
    num = out.get('num', index + 1)

    raw_text = clean_ocr_text(out.get('text') or '')
    structured = bool(out.get('structured')) or bool(out.get('contenus'))
    synth = synthesize_chapter_text(out) if structured else ''
    if not synth and out.get('text_structured'):
        synth = clean_ocr_text(str(out['text_structured']))

    # Chapitres enrichis : le texte propre = champs structurés (pas l'OCR brut)
    if structured and synth and len(synth) >= 80:
        final_text = synth
    elif duplicate_blob and synth and len(synth) >= 80:
        final_text = synth
    elif duplicate_blob:
        if split_map and num in split_map and len(split_map[num]) >= 200:
            final_text = split_map[num]
        elif synth:
            final_text = synth
        else:
            chunk_size = max(500, len(shared_text) // max(total, 1))
            start = index * chunk_size
            final_text = clean_ocr_text(shared_text[start:start + CHAPTER_TEXT_MAX])
    elif len(raw_text) > CHAPTER_TEXT_MAX and synth:
        final_text = synth + '\n\n---\n\n' + raw_text[:2000]
    elif len(raw_text) > CHAPTER_TEXT_MAX:
        final_text = raw_text[:CHAPTER_TEXT_MAX]
    elif raw_text:
        final_text = raw_text
    else:
        final_text = synth

    if len(final_text) > CHAPTER_TEXT_MAX:
        final_text = final_text[:CHAPTER_TEXT_MAX].rstrip() + '\n…'

    out['text'] = final_text
    if synth and final_text == synth:
        out.pop('text_structured', None)  # évite doublon
    elif synth:
        out['text_structured'] = synth

    # Pages : corriger si toutes identiques
    pages = out.get('pages') or []
    if duplicate_blob and pages:
        out['pages'] = _estimate_pages(pages, index, total)

    # Champs structurés propres
    out['competences'] = [clean_ocr_text(str(x)) for x in (out.get('competences') or []) if str(x).strip()]
    out['contenus'] = [clean_ocr_text(str(x)) for x in (out.get('contenus') or []) if str(x).strip()]
    defs_out = []
    for d in out.get('definitions') or []:
        if isinstance(d, dict) and d.get('term'):
            defs_out.append({
                'term': fix_title(str(d['term'])),
                'def': clean_ocr_text(str(d.get('def', ''))),
            })
    out['definitions'] = defs_out
    if out.get('summary'):
        out['summary'] = clean_ocr_text(str(out['summary']))
    out['structured'] = structured or bool(out['contenus'])
    out['cleaned'] = True
    return out


def clean_chapters_data(data: dict) -> dict:
    """Nettoie un fichier chapters_{subject}.json complet."""
    chapters = data.get('chapters') or []
    if not chapters:
        return data

    # Détecter texte dupliqué
    hashes = [_text_hash(c.get('text', '')) for c in chapters]
    unique_hashes = set(h for h in hashes if h)
    duplicate_blob = len(unique_hashes) == 1 and len(chapters) > 1 and len(chapters[0].get('text', '')) > 500

    shared_text = chapters[0].get('text', '') if duplicate_blob else ''
    split_map = _split_text_by_titles(shared_text, chapters) if duplicate_blob else {}

    cleaned_chapters = []
    for i, ch in enumerate(chapters):
        # Ignorer chapitres vides sans structure
        has_content = (
            len((ch.get('text') or '').strip()) > 30
            or ch.get('contenus')
            or ch.get('summary')
            or ch.get('competences')
        )
        if not has_content:
            continue
        cleaned_chapters.append(
            sanitize_chapter(
                ch,
                index=i,
                total=len(chapters),
                shared_text=shared_text,
                duplicate_blob=duplicate_blob,
                split_map=split_map,
            )
        )

    # Renuméroter
    for i, ch in enumerate(cleaned_chapters, 1):
        ch['num'] = i

    data['chapters'] = cleaned_chapters
    data['total_chapters'] = len(cleaned_chapters)
    data['cleaned_at'] = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')
    data['clean_version'] = 1
    return data


def _is_exam_consigne(q: dict) -> bool:
    text = (q.get('text') or '').strip()
    if not text or len(text) < 10:
        return True
    if _EXAM_CONSIGNE_PATTERNS.search(text):
        return True
    if q.get('points', 0) == 0 and len(text) < 80 and not re.search(r'\?', text):
        if re.match(r'^\d+\s*[-.)]', text):
            return True
    return False


def clean_exam_parts(parts: list) -> list:
    """Filtre les fausses questions (consignes OCR) dans parts regex."""
    cleaned = []
    for part in parts or []:
        part = dict(part)
        new_sections = []
        for sec in part.get('sections') or []:
            sec = dict(sec)
            new_themes = []
            for theme in sec.get('themes') or []:
                theme = dict(theme)
                qs = [
                    q for q in (theme.get('questions') or [])
                    if isinstance(q, dict) and not _is_exam_consigne(q)
                ]
                if qs:
                    theme['questions'] = qs
                    new_themes.append(theme)
            if new_themes:
                sec['themes'] = new_themes
                new_sections.append(sec)
        if new_sections:
            part['sections'] = new_sections
            cleaned.append(part)
    return cleaned


def clean_exam_entry(exam: dict) -> dict:
    """Nettoie une entrée d'examen."""
    out = dict(exam)
    raw = out.get('text') or ''
    if raw:
        cleaned = clean_ocr_text(raw)
        if len(cleaned) > EXAM_TEXT_MAX:
            cleaned = cleaned[:EXAM_TEXT_MAX] + '\n…'
        out['text'] = cleaned
        out['chars'] = len(cleaned)

    # Si items rebuild disponibles : parts regex = bruit, on les retire
    if out.get('rebuilt') and out.get('items'):
        out.pop('parts', None)
        out['structured'] = True
        out['parts_source'] = 'removed_after_rebuild'
    elif out.get('parts'):
        filtered = clean_exam_parts(out['parts'])
        if filtered:
            out['parts'] = filtered
        else:
            out.pop('parts', None)
            out['structured'] = False

    out['cleaned'] = True
    return out


def clean_exams_data(data: dict) -> dict:
    exams = [clean_exam_entry(e) for e in (data.get('exams') or [])]
    data['exams'] = exams
    data['total_chars'] = sum(len(e.get('text', '')) for e in exams)
    data['cleaned_at'] = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')
    data['clean_version'] = 1
    return data


def deep_fix_strings(obj: Any) -> Any:
    """Applique fix_mojibake + clean_ocr récursivement."""
    if isinstance(obj, str):
        return clean_ocr_text(fix_mojibake(obj))
    if isinstance(obj, list):
        return [deep_fix_strings(x) for x in obj]
    if isinstance(obj, dict):
        return {k: deep_fix_strings(v) for k, v in obj.items()}
    return obj


def clean_exo_file(data: dict) -> dict:
    return deep_fix_strings(data)


def repair_exo_chimie(path: Path) -> dict:
    """Reconstruit exo_chimie.json depuis les objets valides + markdown tail."""
    import re
    from collections import defaultdict

    raw_bytes = path.read_bytes()
    raw = None
    for enc in ('utf-8', 'utf-8-sig', 'cp1252', 'latin-1'):
        try:
            raw = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raw = raw_bytes.decode('utf-8', errors='replace')

    # Import tardif pour éviter cycle Django
    from core.exo_loader import _iter_chimie_json_exos, _parse_chimie_markdown_tail

    by_chapter: dict[str, list] = defaultdict(list)
    seen_keys: set[tuple] = set()

    for pos, exo in _iter_chimie_json_exos(raw):
        chunk_before = raw[:pos]
        titre_m = None
        for tm in re.finditer(r'"titre"\s*:\s*"([^"]+)"', chunk_before):
            titre_m = tm
        chapter = fix_title(titre_m.group(1)) if titre_m else 'Chimie'
        key = (chapter, exo.get('num'), (exo.get('enonce') or '')[:80])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        by_chapter[chapter].append(deep_fix_strings(exo))

    tail_m = re.search(r'\n\s*id\s*:\s*11\b', raw)
    if tail_m:
        tail_exos = _parse_chimie_markdown_tail(raw[tail_m.start():], 'chimie')
        for ex in tail_exos:
            ch = fix_title(ex.get('chapter', 'Chimie organique'))
            by_chapter[ch].append({
                'num': len(by_chapter[ch]) + 1,
                'type': 'real' if ex.get('source') else 'genere',
                'source': ex.get('source'),
                'enonce': ex.get('enonce', ''),
                'questions': ex.get('questions', []),
                'reponses': ex.get('reponses', {}),
            })

    chapitres = []
    for i, (titre, exercices) in enumerate(by_chapter.items(), 1):
        for j, ex in enumerate(exercices, 1):
            if not ex.get('num'):
                ex['num'] = j
        chapitres.append({
            'id': i,
            'titre': titre,
            'description': '',
            'exercices': exercices,
        })

    meta = {
        'titre': "Banque d'exercices Chimie – Baccalauréat Haïti",
        'source': 'Reconstruit depuis exo_chimie.json (objets valides)',
        'chapitres_couverts': len(chapitres),
        'repaired': True,
    }
    return {'metadata': meta, 'chapitres': chapitres}


def read_text_auto(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ('utf-8', 'utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def write_json(path: Path, data: dict) -> int:
    """Écrit JSON indenté, retourne taille fichier."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path.stat().st_size


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)
