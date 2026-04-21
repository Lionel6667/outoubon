"""
Management command : rebuild_exam_json
=======================================
Relit le texte brut de chaque examen dans les fichiers JSON
et utilise l'IA pour le restructurer proprement en items exploitables
(questions, exercices avec sous-questions, dissertations, QCM, etc.)

Selon la matière :
  EXERCICE (maths, physique, chimie, svt) :
    → items de type "exercice" : contexte + liste de sous-questions
  QUESTION (francais, philosophie, histoire, anglais, economie,
             informatique, espagnol, art) :
    → items de type "question" (simple) ou "dissertation" ou "question_texte"

Usage :
    python manage.py rebuild_exam_json                        # tout reconstruire
    python manage.py rebuild_exam_json --subject maths        # une seule matière
    python manage.py rebuild_exam_json --subject maths --force  # écraser même si déjà fait
    python manage.py rebuild_exam_json --limit 5              # tester sur 5 exams max
    python manage.py rebuild_exam_json --dry-run              # voir sans écrire

Résultat : les champs 'items' et 'rebuilt' sont ajoutés à chaque examen.
"""

import json
import re
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

# ── Sujets qui portent des EXERCICES (problèmes multi-étapes) ───────────────
EXERCISE_SUBJECTS = {'maths', 'physique', 'chimie', 'svt'}

# ── Sujets qui portent des QUESTIONS (réponses directes, dissertations…) ─────
QUESTION_SUBJECTS = {'francais', 'philosophie', 'histoire', 'anglais',
                     'economie', 'informatique', 'espagnol', 'art'}

ALL_SUBJECTS = list(EXERCISE_SUBJECTS | QUESTION_SUBJECTS)


# ─── Appel IA centralisé ──────────────────────────────────────────────────────

def _call_ai(prompt: str, system: str = '', max_tokens: int = 4000, retries: int = 3) -> str:
    """Appel Groq / OpenAI via le settings Django."""
    from groq import Groq
    client = Groq(api_key=settings.GROQ_API_KEY)
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model='openai/gpt-oss-20b',
                messages=messages,
                max_tokens=max_tokens,
            )
            msg = resp.choices[0].message
            content = msg.content or ''
            # Fallback: some attempts return empty content but filled reasoning
            if not content.strip():
                reasoning = getattr(msg, 'reasoning', '') or ''
                # Extract JSON from reasoning if present
                if '[' in reasoning or '{' in reasoning:
                    content = reasoning
            if content.strip():
                return content
            # Empty response — retry after small delay
            if attempt < retries - 1:
                time.sleep(3)
        except Exception as e:
            err_str = str(e).lower()
            # Rate limit: wait longer before retry
            if 'rate' in err_str or '429' in err_str or 'quota' in err_str:
                wait = 30 * (attempt + 1)  # 30s, 60s, 90s
                time.sleep(wait)
            elif 'connection' in err_str or 'timeout' in err_str or '503' in err_str:
                wait = 10 * (attempt + 1)  # 10s, 20s, 30s
                time.sleep(wait)
            else:
                if attempt < retries - 1:
                    time.sleep(5)
                else:
                    raise
    return ''


def _fix_latex_in_json(text: str) -> str:
    """
    Converts LaTeX \(...\) and \[...\] delimiters → $...$  inside a JSON string.
    Also fixes common invalid JSON escape sequences from AI LaTeX output.
    """
    # Convert \( math \) → $math$ and \[ math \] → $$math$$
    text = re.sub(r'\\\(([^)]*?)\\\)', lambda m: '$' + m.group(1) + '$', text)
    text = re.sub(r'\\\[([^\]]*?)\\\]', lambda m: '$$' + m.group(1) + '$$', text)
    # Replace remaining invalid backslash escapes that aren't valid JSON:
    # Valid JSON escapes: \" \\ \/ \b \f \n \r \t \uXXXX
    # Fix \d \g \s \l \c \p \v \f(already valid) \a and other unknown
    def fix_escape(m):
        ch = m.group(1)
        if ch in ('"', '\\', '/', 'b', 'f', 'n', 'r', 't'):
            return m.group(0)  # already valid
        if ch == 'u':
            return m.group(0)  # \uXXXX — valid
        # Replace invalid escape with just the character
        return ch
    text = re.sub(r'\\(.)', fix_escape, text)
    return text


def _parse_json_from_text(text: str):
    """Extrait le premier objet/tableau JSON valide du texte de l'IA."""
    text = re.sub(r'```[a-z]*\n?', '', text).strip().rstrip('`').strip()
    # Try array first, then object
    for pattern in [r'\[[\s\S]+\]', r'\{[\s\S]+\}']:
        m = re.search(pattern, text)
        if m:
            raw = m.group(0)
            # First try direct parse
            try:
                return json.loads(raw)
            except Exception:
                pass
            # Fix trailing commas
            fixed = re.sub(r',\s*([}\]])', r'\1', raw)
            try:
                return json.loads(fixed)
            except Exception:
                pass
            # Fix LaTeX escapes (AI writes \( \) delimiters which are invalid JSON)
            latex_fixed = _fix_latex_in_json(fixed)
            try:
                return json.loads(latex_fixed)
            except Exception:
                pass
            # Last resort: also fix the original raw with LaTeX fix
            latex_fixed2 = _fix_latex_in_json(raw)
            try:
                return json.loads(latex_fixed2)
            except Exception:
                pass
            # Truncation recovery: try to close the JSON array by trimming to last complete object
            for candidate in [latex_fixed, latex_fixed2, fixed, raw]:
                recovered = _recover_truncated_array(candidate)
                if recovered:
                    return recovered
    return None


def _recover_truncated_array(text: str):
    """Try to salvage a truncated JSON array by trimming to last complete {...} object."""
    try:
        # Find all complete JSON objects in the array
        items = []
        depth = 0
        in_string = False
        escape_next = False
        start = None
        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{' and depth == 0:
                start = i
                depth = 1
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    obj_str = text[start:i+1]
                    try:
                        items.append(json.loads(obj_str))
                    except Exception:
                        pass
                    start = None
        return items if items else None
    except Exception:
        return None


# ─── Prompts IA ──────────────────────────────────────────────────────────────

SYSTEM_EXPERT = (
    "Tu es un pédagogue expert en éducation haïtienne (niveau Terminale BAC). "
    "Tu analyses des examens officiels haïtiens et les structures proprement. "
    "Tu réponds UNIQUEMENT en JSON valide, sans texte autour, sans hors-sujet. "
    "Chaque item JSON doit être complet et compréhensible seul. "
    "Langue : français (sauf pour les matières créole/anglais — respecte la langue de l'examen)."
)


def _build_fallback_prompt(subject: str, year: str, filename: str, is_exercise: bool) -> str:
    """Prompt de secours quand le texte brut est trop corrompu : génère depuis zéro."""
    if is_exercise:
        return f"""Crée 2 exercices typiques du BAC Haïti {year} — {subject.upper()}, niveau Terminale.
Ces exercices doivent correspondre au programme officiel haïtien et être de vraie difficulté BAC.

Réponds UNIQUEMENT avec ce JSON array :
[
  {{
    "type": "exercice",
    "theme": "Titre du chapitre",
    "intro": "Énoncé complet de l'exercice avec toutes les données nécessaires.",
    "questions": ["a) Première question", "b) Deuxième question", "c) Troisième question"],
    "difficulte": "moyen",
    "source": "Bac Haïti {year} — {filename}"
  }}
]
Formules : notation KaTeX $...$ uniquement. Minimum 3 questions par exercice."""
    else:
        return f"""Crée 4 questions typiques du BAC Haïti {year} — {subject.upper()}, niveau Terminale.
Varie les types : question directe, question sur texte, dissertation, QCM.

Réponds UNIQUEMENT avec ce JSON array :
[
  {{
    "type": "question|question_texte|dissertation|qcm",
    "theme": "Compétence évaluée",
    "enonce": "Question complète",
    "difficulte": "moyen",
    "source": "Bac Haïti {year} — {filename}"
  }}
]"""


def _build_exercise_prompt(raw_text: str, subject: str, year: str, filename: str) -> str:
    """Prompt pour matières à exercices : maths, physique, chimie, svt."""
    return f"""Voici le texte brut d'un examen officiel du Bac Haïti — {subject.upper()} {year} ({filename}).

Le texte peut contenir des artefacts de scan (colonnes mélangées, espaces parasites, caractères corrompus, questions entremêlées). Ignore ces artefacts et concentre-toi sur le SENS pédagogique.

=== TEXTE BRUT ===
{raw_text[:7000]}
=== FIN TEXTE ===

TA MISSION : Extrais les exercices de la PARTIE B (ou équivalent) de cet examen.
- Ignore totalement la PARTIE A (recopier et compléter, questions à trous, fill-in-blank).
- Chaque exercice doit avoir : un contexte complet + des sous-questions logiquement enchaînées.
- Si le texte est trop corrompu pour extraire un exercice réel, INVENTE un exercice réaliste de niveau Terminale {subject.upper()} style BAC Haïti, fidèle au niveau et à l'année.
- Fusionne les parties fragmentées qui appartiennent au même exercice.
- Formules mathématiques : utilise UNIQUEMENT la notation KaTeX $expression$ (inline) ou $$expression$$ (bloc). N'utilise JAMAIS \( \) ou \[ \] — ces backslashes cassent le JSON.
- Ne duplique pas les exercices.
- Retourne EXACTEMENT 2 exercices (pas plus, pas moins) pour garder le JSON compact.

Réponds UNIQUEMENT avec ce JSON array (rien d'autre) :
[
  {{
    "type": "exercice",
    "theme": "Titre court du sujet (ex: Étude de fonction, Suites, Nombres complexes, Probabilités)",
    "intro": "Énoncé complet avec toutes les données numériques, définitions, contexte. En français correct.",
    "questions": [
      "a) Première sous-question complète et autonome",
      "b) Deuxième sous-question complète",
      "c) Troisième sous-question",
      "d) Quatrième sous-question si applicable"
    ],
    "difficulte": "facile|moyen|difficile",
    "source": "Bac Haïti {year} — {filename}"
  }}
]

Règles strictes :
- Minimum 3 sous-questions par exercice, maximum 8.
- intro doit être autonome (compréhensible sans contexte externe).
- Chaque question doit être complète (pas de "suite de la précédente").
- Ne mets PAS de questions de type "recopier et compléter", "compléter le tableau" ou fill-in-blank."""


def _build_question_prompt(raw_text: str, subject: str, year: str, filename: str) -> str:
    """Prompt pour matières à questions : francais, philo, histoire, etc."""
    return f"""Voici le texte brut d'un examen officiel du Bac Haïti — {subject.upper()} {year} ({filename}).

Le texte peut contenir des artefacts de scan. Ignore-les et concentre-toi sur le SENS pédagogique.

=== TEXTE BRUT ===
{raw_text[:7000]}
=== FIN TEXTE ===

TA MISSION : Extrais les items pédagogiques exploitables de cet examen.

Types d'items possibles :
- "question" : question courte/directe à réponse rédigée (ex: "Expliquez le concept X")
- "question_texte" : question basée sur un texte fourni (inclure le TEXTE dans le champ "texte")  
- "dissertation" : sujet de dissertation complet avec plan/consigne (philo, francais)
- "qcm" : question à choix multiples (si l'examen en contient)
- "production_ecrite" : exercice de rédaction/production (sujet + consignes)

Règles :
- Fusionne les items fragmentés qui appartiennent ensemble.
- Chaque item doit être COMPLET et compréhensible SEUL.
- Pour "question_texte" : inclure intégralement le texte dans le champ "texte".
- Pour "dissertation" : inclure le sujet complet, les pistes et consignes.
- Ne mets PAS d'items incomplets ou tronqués.
- Ignore les consignes générales d'examen (silence, durée, calculatrice interdite, etc.)
- Retourne entre 3 et 6 items maximum pour garder le JSON compact.

Réponds UNIQUEMENT avec ce JSON array (rien d'autre) :
[
  {{
    "type": "question|question_texte|dissertation|qcm|production_ecrite",
    "theme": "Thème ou compétence évaluée (ex: Compréhension de texte, Argumentation, Vocabulaire)",
    "enonce": "Texte complet de la question, du sujet ou des consignes",
    "texte": "Texte support si question_texte (sinon champ absent ou null)",
    "options": ["A: ...", "B: ...", "C: ...", "D: ..."],
    "reponse": "Réponse correcte ou éléments de réponse attendus (si applicable)",
    "difficulte": "facile|moyen|difficile",
    "source": "Bac Haïti {year} — {filename}"
  }}
]

Note sur les champs optionnels :
- "texte" : uniquement pour question_texte
- "options" : uniquement pour qcm  
- "reponse" : si la réponse est connue, sinon omets le champ"""


# ─── Traitement d'un seul examen ─────────────────────────────────────────────

def _rebuild_exam(exam: dict, subject: str, dry_run: bool = False) -> dict:
    """
    Prend un examen dict (avec 'text', 'year', 'file') et retourne le même dict
    enrichi avec un champ 'items' contenant les items IA reconstruits.
    """
    raw_text = exam.get('text', '') or exam.get('cleaned', '')
    # Ensure raw_text is a string (some JSON values may be bool/None/list)
    if isinstance(raw_text, list):
        raw_text = ' '.join(str(x) for x in raw_text if x)
    elif not isinstance(raw_text, str):
        raw_text = str(raw_text) if raw_text else ''
    if not raw_text or len(raw_text.strip()) < 100:
        exam['items'] = []
        exam['rebuilt'] = False
        exam['rebuild_error'] = 'empty_text'
        return exam

    year = str(exam.get('year', '?'))
    filename = exam.get('file', 'inconnu')

    is_exercise_subject = subject in EXERCISE_SUBJECTS
    if is_exercise_subject:
        prompt = _build_exercise_prompt(raw_text, subject, year, filename)
    else:
        prompt = _build_question_prompt(raw_text, subject, year, filename)

    if dry_run:
        exam['items'] = [{'type': 'DRY_RUN', 'theme': 'not_executed'}]
        exam['rebuilt'] = False
        return exam

    try:
        raw_response = _call_ai(prompt, system=SYSTEM_EXPERT, max_tokens=4000)
        items = _parse_json_from_text(raw_response)

        # If empty response or no items, try a simpler "generate from scratch" prompt
        if not raw_response.strip() or not isinstance(items, list) or len(items) == 0:
            fallback_prompt = _build_fallback_prompt(subject, year, filename, is_exercise_subject)
            raw_response = _call_ai(fallback_prompt, system=SYSTEM_EXPERT, max_tokens=3000)
            items = _parse_json_from_text(raw_response)

        if not isinstance(items, list) or len(items) == 0:
            exam['items'] = []
            exam['rebuilt'] = False
            exam['rebuild_error'] = f'parse_failed: {raw_response[:200]}'
            return exam

        # Validate and clean items
        clean_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            itype = item.get('type', 'question')
            # For exercises: must have intro + questions list
            if is_exercise_subject:
                intro = (item.get('intro') or '').strip()
                questions = [str(q).strip() for q in item.get('questions', []) if str(q).strip()]
                if not intro or len(questions) < 2:
                    continue
                clean_items.append({
                    'type': 'exercice',
                    'theme': (item.get('theme') or subject).strip(),
                    'intro': intro,
                    'questions': questions,
                    'difficulte': item.get('difficulte', 'moyen') if item.get('difficulte') in ('facile', 'moyen', 'difficile') else 'moyen',
                    'source': item.get('source', f'Bac Haïti {year} — {filename}'),
                })
            else:
                # For question subjects
                enonce = (item.get('enonce') or '').strip()
                if not enonce:
                    continue
                entry = {
                    'type': itype if itype in ('question', 'question_texte', 'dissertation', 'qcm', 'production_ecrite') else 'question',
                    'theme': (item.get('theme') or '').strip(),
                    'enonce': enonce,
                    'difficulte': item.get('difficulte', 'moyen') if item.get('difficulte') in ('facile', 'moyen', 'difficile') else 'moyen',
                    'source': item.get('source', f'Bac Haïti {year} — {filename}'),
                }
                if item.get('texte'):
                    entry['texte'] = str(item['texte']).strip()
                if item.get('options') and isinstance(item['options'], list):
                    entry['options'] = [str(o).strip() for o in item['options'] if str(o).strip()]
                if item.get('reponse'):
                    entry['reponse'] = str(item['reponse']).strip()
                clean_items.append(entry)

        exam['items'] = clean_items
        exam['rebuilt'] = True
        exam['rebuild_error'] = None

    except Exception as e:
        exam['items'] = []
        exam['rebuilt'] = False
        exam['rebuild_error'] = str(e)

    return exam


# ─── Commande Django ──────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = 'Relit les textes bruts des examens JSON et les restructure avec l\'IA'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='',
                            help='Matière à traiter (ex: maths). Toutes si vide.')
        parser.add_argument('--force', action='store_true',
                            help='Retraiter même les examens déjà reconstruits')
        parser.add_argument('--limit', type=int, default=0,
                            help='Limiter à N examens par matière (test)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Simuler sans appel IA ni écriture')
        parser.add_argument('--delay', type=float, default=1.2,
                            help='Délai en secondes entre les appels IA (défaut 1.2s)')

    def handle(self, *args, **options):
        subject_filter = options['subject'].strip().lower()
        force          = options['force']
        limit          = options['limit']
        dry_run        = options['dry_run']
        delay          = options['delay']

        subjects = [subject_filter] if subject_filter else ALL_SUBJECTS
        # Order: exercise subjects first (most important)
        subjects = sorted(subjects, key=lambda s: (0 if s in EXERCISE_SUBJECTS else 1, s))

        json_dir = Path(settings.BASE_DIR) / 'database' / 'json'

        total_processed = 0
        total_rebuilt   = 0
        total_errors    = 0
        total_skipped   = 0

        for subject in subjects:
            json_path = json_dir / f'exams_{subject}.json'
            if not json_path.exists():
                self.stdout.write(self.style.WARNING(f'  [SKIP] {json_path.name} not found'))
                continue

            with open(json_path, encoding='utf-8', errors='replace') as f:
                data = json.load(f)

            exams = data.get('exams', [])
            if not exams:
                self.stdout.write(self.style.WARNING(f'  [SKIP] {subject}: 0 exams'))
                continue

            # Filter to process
            to_process = [e for e in exams if force or not e.get('rebuilt')]
            if limit > 0:
                to_process = to_process[:limit]

            self.stdout.write(self.style.SUCCESS(
                f'\n[{subject.upper()}] {len(exams)} exams total | '
                f'{len(to_process)} to process | '
                f'{"EXERCISE" if subject in EXERCISE_SUBJECTS else "QUESTION"} type'
            ))

            changed = 0
            for i, exam in enumerate(to_process):
                fname = exam.get('file', '?')
                year  = exam.get('year', '?')
                self.stdout.write(f'  [{i+1}/{len(to_process)}] {fname} ({year})... ', ending='')

                exam_idx = exams.index(exam)
                updated_exam = _rebuild_exam(exam, subject, dry_run=dry_run)
                exams[exam_idx] = updated_exam

                items_count = len(updated_exam.get('items', []))
                rebuilt = updated_exam.get('rebuilt', False)
                err = updated_exam.get('rebuild_error')

                if rebuilt:
                    self.stdout.write(self.style.SUCCESS(f'OK ({items_count} items)'))
                    total_rebuilt += 1
                    changed += 1
                elif dry_run:
                    self.stdout.write(self.style.WARNING('DRY-RUN'))
                    total_skipped += 1
                else:
                    self.stdout.write(self.style.ERROR(f'FAIL — {err}'))
                    total_errors += 1

                total_processed += 1

                # Save incrementally every 5 exams (protect against crashes)
                if not dry_run and changed > 0 and changed % 5 == 0:
                    data['exams'] = exams
                    data['rebuilt_count'] = sum(1 for e in exams if e.get('rebuilt'))
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    self.stdout.write(self.style.WARNING(f'    [auto-save after {changed} exams]'))

                # Respect rate limits
                if not dry_run and i < len(to_process) - 1:
                    time.sleep(delay)

            # Save back
            if not dry_run and changed > 0:
                data['exams'] = exams
                data['rebuilt_count'] = sum(1 for e in exams if e.get('rebuilt'))
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.stdout.write(self.style.SUCCESS(
                    f'  → Saved {json_path.name} ({changed} exams updated)'
                ))
            elif dry_run:
                self.stdout.write(self.style.WARNING('  → DRY-RUN: nothing written'))

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS(
            f'DONE: {total_processed} processed | {total_rebuilt} rebuilt | '
            f'{total_skipped} skipped | {total_errors} errors'
        ))
