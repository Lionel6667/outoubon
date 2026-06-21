"""
parse_chapter_structure — Enrichit les chapitres du programme officiel avec l'IA.

Lit les chapters_{subject}.json générés par build_chapter_json,
appelle l'IA (Groq) pour structurer chaque chapitre en :
  - titre officiel clair
  - compétences (ce que l'élève doit SAVOIR FAIRE)
  - contenus (notions clés abordées)
  - définitions (vocabulaire important)
  - résumé

Pour les sujets avec 1 seul bloc brut (chimie, physique, etc.),
l'IA divise ET structure en plusieurs chapitres.

Seulement ~46 appels IA au total (vs 3000+ pour les examens).
Durée estimée : 2-4 minutes.

Usage:
    py manage.py parse_chapter_structure --all
    py manage.py parse_chapter_structure --subject maths
    py manage.py parse_chapter_structure --all --redo
    py manage.py parse_chapter_structure --subject svt --dry-run
"""

import json
import re
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core import gemini

SUBJECTS = [
    'anglais', 'art', 'chimie', 'economie', 'espagnol',
    'francais', 'histoire', 'informatique', 'maths',
    'philosophie', 'physique', 'svt',
]

SUBJECT_LABELS = {
    'maths':       'Mathématiques',
    'physique':    'Physique',
    'chimie':      'Chimie',
    'svt':         'SVT (Biologie & Géologie)',
    'francais':    'Français / Créole',
    'philosophie': 'Philosophie',
    'histoire':    'Histoire & Sciences Sociales',
    'anglais':     'Anglais',
    'economie':    'Économie',
    'informatique':'Informatique',
    'espagnol':    'Espagnol',
    'art':         'Arts Plastiques',
}

_AI_DELAY = 0.5  # secondes entre appels IA


# ═══════════════════════════════════════════════════════════════════════════════
# Prompts IA
# ═══════════════════════════════════════════════════════════════════════════════

_PROMPT_SPLIT = """Tu es expert du programme officiel du BAC Haiti (Terminale / 4ème année Nouveau Secondaire).

Voici le programme officiel de {label} :
---
{text}
---

Analyse ce programme et génère la liste COMPLÈTE des chapitres du cours.
Pour chaque chapitre, donne :
- "num" : numéro d'ordre (1, 2, 3...)
- "title" : titre officiel du chapitre (court, précis, en français)
- "competences" : liste de 3-6 compétences (ce que l'élève doit savoir FAIRE)
- "contenus" : liste de 3-8 notions/points clés abordés
- "definitions" : liste de 3-5 définitions importantes, format [{{"term":"...","def":"..."}}]
- "summary" : résumé en 2-3 phrases du chapitre

IMPORTANT :
- Base-toi UNIQUEMENT sur le programme fourni
- Génère TOUS les chapitres (généralement 5-10 chapitres par matière)
- Regroupe les sous-thèmes connexes en chapitres cohérents
- Les compétences commencent par un verbe d'action (Identifier, Calculer, Analyser...)

Réponds UNIQUEMENT avec ce JSON (pas de texte avant/après) :
[{{"num":1,"title":"...","competences":["..."],"contenus":["..."],"definitions":[{{"term":"...","def":"..."}}],"summary":"..."}}]"""

_PROMPT_ENRICH = """Tu es expert du programme officiel du BAC Haiti (Terminale).

Voici les données brutes d'un chapitre de {label} :
Titre actuel : {title}
Matière : {matiere}
Contenu brut :
---
{text}
---

Enrichis ce chapitre. Fournis :
- "title" : titre officiel amélioré si nécessaire (garde le sens)
- "competences" : liste de 3-6 compétences (verbe d'action : Identifier, Calculer...)
- "contenus" : liste de 3-8 notions clés du chapitre
- "definitions" : 3-5 définitions importantes [{{"term":"...","def":"..."}}]
- "summary" : résumé en 2-3 phrases

Réponds UNIQUEMENT avec ce JSON (pas de texte avant/après) :
{{"title":"...","competences":["..."],"contenus":["..."],"definitions":[{{"term":"...","def":"..."}}],"summary":"..."}}"""


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction JSON depuis réponse IA
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_json_array(text: str) -> list:
    """Extrait un array JSON depuis une réponse IA (tolère le markdown)."""
    text = re.sub(r'```[a-z]*\s*', '', text).strip()
    # Cherche le premier [...]
    m = re.search(r'\[[\s\S]*\]', text)
    if not m:
        return []
    try:
        result = json.loads(m.group(0))
        return result if isinstance(result, list) else []
    except Exception:
        pass
    # Fallback : extraire objet par objet
    objects = []
    for obj_m in re.finditer(r'\{(?:[^{}]|\{[^{}]*\})*\}', m.group(0)):
        try:
            obj = json.loads(obj_m.group(0))
            if isinstance(obj, dict) and ('title' in obj or 'num' in obj):
                objects.append(obj)
        except Exception:
            pass
    return objects


def _extract_json_object(text: str) -> dict:
    """Extrait un objet JSON depuis une réponse IA."""
    text = re.sub(r'```[a-z]*\s*', '', text).strip()
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return {}
    try:
        result = json.loads(m.group(0))
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _ensure_list(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.strip():
        return [val]
    return []


def _sanitize_chapter(ch: dict, fallback_num: int = 1) -> dict:
    """Normalise un chapitre enrichi par l'IA."""
    return {
        'num':         ch.get('num', fallback_num),
        'title':       str(ch.get('title', 'Chapitre')).strip(),
        'matiere':     ch.get('matiere', ''),
        'source_pdf':  ch.get('source_pdf', ''),
        'competences': _ensure_list(ch.get('competences', [])),
        'contenus':    _ensure_list(ch.get('contenus', [])),
        'definitions': [d for d in _ensure_list(ch.get('definitions', []))
                        if isinstance(d, dict) and 'term' in d],
        'summary':     str(ch.get('summary', '')).strip(),
        'structured':  True,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Traitement
# ═══════════════════════════════════════════════════════════════════════════════

def _structure_programme_block(chapter: dict, subject_label: str, verbose=True) -> list:
    """
    Un seul bloc brut "Programme complet" → demande à l'IA de le découper
    et structurer en plusieurs chapitres.
    Retourne [{structured_chapter}, ...]
    """
    text = chapter.get('text', '')[:4500]   # max ~4500 chars pour tenir dans le contexte
    if not text.strip():
        return []

    prompt = _PROMPT_SPLIT.format(label=subject_label, text=text)
    try:
        raw = gemini._call_json(prompt, max_tokens=3000)
        chapters_ai = _extract_json_array(raw)
        if not chapters_ai:
            return []
        result = []
        for i, ch_ai in enumerate(chapters_ai, 1):
            ch = _sanitize_chapter(ch_ai, i)
            ch['matiere']    = chapter.get('matiere', '')
            ch['source_pdf'] = chapter.get('source_pdf', '')
            result.append(ch)
        return result
    except Exception as e:
        if verbose:
            print(f'    ERREUR IA split: {e}')
        return []


def _enrich_chapter(chapter: dict, subject_label: str, verbose=True) -> dict:
    """
    Un chapitre avec texte brut → demande à l'IA de l'enrichir.
    Retourne le chapitre enrichi.
    """
    text = chapter.get('text', '')[:3000]
    title = chapter.get('title', 'Chapitre')
    matiere = chapter.get('matiere', subject_label)

    prompt = _PROMPT_ENRICH.format(
        label=subject_label,
        title=title,
        matiere=matiere,
        text=text[:2500],
    )
    try:
        raw = gemini._call_json(prompt, max_tokens=1500)
        ai_data = _extract_json_object(raw)
        if not ai_data:
            return {**chapter, 'structured': False}
        enriched = {**chapter}
        if ai_data.get('title') and len(ai_data['title']) > 3:
            enriched['title'] = ai_data['title'].strip()
        enriched['competences'] = _ensure_list(ai_data.get('competences', []))
        enriched['contenus']    = _ensure_list(ai_data.get('contenus', []))
        enriched['definitions'] = [d for d in _ensure_list(ai_data.get('definitions', []))
                                   if isinstance(d, dict) and 'term' in d]
        enriched['summary']     = str(ai_data.get('summary', '')).strip()
        enriched['structured']  = True
        return enriched
    except Exception as e:
        if verbose:
            print(f'    ERREUR IA enrich: {e}')
        return {**chapter, 'structured': False}


# ═══════════════════════════════════════════════════════════════════════════════
class Command(BaseCommand):
    help = 'Enrichit chapters_{subject}.json avec l\'IA (compétences, contenus, définitions, résumé)'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='',
                            help='Ex: svt, maths, chimie...')
        parser.add_argument('--all',   action='store_true')
        parser.add_argument('--redo',  action='store_true',
                            help='Re-structure même si déjà fait')
        parser.add_argument('--dry-run', action='store_true',
                            help='Montre ce qui serait fait sans appeler l\'IA')

    def _out(self, msg: str):
        """Écrit dans stdout en ignorant les erreurs d'encodage."""
        try:
            self.stdout.write(msg)
        except (UnicodeEncodeError, UnicodeDecodeError):
            self.stdout.write(msg.encode('ascii', 'replace').decode())

    def handle(self, *args, **options):
        db_path  = Path(getattr(settings, 'COURSE_DB_PATH', ''))
        json_dir = db_path / 'json'

        only    = options['subject'].strip().lower()
        do_all  = options['all']
        redo    = options['redo']
        dry_run = options['dry_run']

        subjects = SUBJECTS if do_all else ([only] if only else [])
        if not subjects:
            self._out('Utilise --subject NOM ou --all')
            return

        grand_total_chaps = 0
        grand_total_ai    = 0

        for subject in subjects:
            json_file = json_dir / f'chapters_{subject}.json'
            if not json_file.exists():
                self._out(f'[ABSENT] {json_file.name} -- lance d\'abord build_chapter_json')
                continue

            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            chapters  = data.get('chapters', [])
            label     = SUBJECT_LABELS.get(subject, subject.capitalize())
            ai_calls  = 0

            self._out(f'\n[{subject.upper()}] {label} -- {len(chapters)} chapitres')

            new_chapters = []
            for i, ch in enumerate(chapters):
                is_raw   = ch.get('title', '').lower() in ('programme complet', 'programme', '')
                is_done  = ch.get('structured', False)
                text_len = len(ch.get('text', ''))

                if is_done and not redo:
                    new_chapters.append(ch)
                    continue

                # Bloc "Programme complet" → split + enrich via IA
                if is_raw and text_len > 100:
                    if dry_run:
                        self._out(
                            f'  [DRY-RUN] Bloc brut ({text_len} chars) -> split+enrich via IA'
                        )
                        new_chapters.append(ch)
                        continue
                    self._out(f'  Bloc brut ({text_len} chars) -> decoupage IA...')
                    result = _structure_programme_block(ch, label)
                    ai_calls += 1
                    time.sleep(_AI_DELAY)
                    if result:
                        self._out(f'    -> {len(result)} chapitres generes')
                        new_chapters.extend(result)
                    else:
                        self._out(f'    -> ECHEC, conserve brut')
                        new_chapters.append({**ch, 'structured': False})
                    continue

                # Chapitre ayant du texte → enrichir
                if text_len > 30:
                    if dry_run:
                        title_short = ch.get('title','')[:50].encode('ascii','replace').decode()
                        self._out(
                            f'  [DRY-RUN] #{ch.get("num","?")} {title_short} '
                            f'({text_len} chars) -> enrich via IA'
                        )
                        new_chapters.append(ch)
                        continue
                    title_short = ch.get('title','')[:45].encode('ascii','replace').decode()
                    self._out(f'  #{ch.get("num","?"):02} {title_short} ({text_len} chars)...')
                    enriched = _enrich_chapter(ch, label)
                    ai_calls += 1
                    time.sleep(_AI_DELAY)
                    n_comp = len(enriched.get('competences', []))
                    n_cont = len(enriched.get('contenus', []))
                    ok = enriched.get('structured', False)
                    self._out(f'    -> {"OK" if ok else "FAIL"} ({n_comp}comp, {n_cont}cont)')
                    new_chapters.append(enriched)
                else:
                    # Chapitre vide/trop court : garder tel quel
                    new_chapters.append({**ch, 'structured': False})

            if not dry_run:
                data['chapters']       = new_chapters
                data['total_chapters'] = len(new_chapters)
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._out(
                    f'  SAUVEGARDE {json_file.name} -- '
                    f'{len(new_chapters)} chapitres, {ai_calls} appels IA'
                )

            grand_total_chaps += len(new_chapters)
            grand_total_ai    += ai_calls

        flag = ' [DRY-RUN]' if dry_run else ''
        self._out(
            f'\nRESULTAT{flag}: {grand_total_chaps} chapitres total | {grand_total_ai} appels IA'
        )
