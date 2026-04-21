#!/usr/bin/env python3
"""
convert_quizzes.py
~~~~~~~~~~~~~~~~~~
Parse markdown quiz files → proper JSON + shuffle options + improve explanations via Groq.

Usage: python convert_quizzes.py
"""
import copy, json, os, random, re, sys, time

# ── Django setup (for settings.GROQ_API_KEY) ──────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
import django; django.setup()
from django.conf import settings
from groq import Groq

client = Groq(api_key=settings.GROQ_API_KEY)
MODEL  = 'openai/gpt-oss-120b'

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database')

FILES = {
    'quiz_SVT.json':         {'subject': 'svt',          'label': 'SVT / Biologie'},
    'quiz_sc_social.json':   {'subject': 'histoire',     'label': 'Sciences Sociales / Histoire'},
    'quiz_physique.json':    {'subject': 'physique',     'label': 'Physique'},
    'quiz_philosophie.json': {'subject': 'philosophie',  'label': 'Philosophie'},
    'quiz_informatique.json':{'subject': 'informatique', 'label': 'Informatique'},
    'quiz_economie.json':    {'subject': 'economie',     'label': 'Économie'},
    'quiz_chimie.json':      {'subject': 'chimie',       'label': 'Chimie'},
    'quiz_art.json':         {'subject': 'art',          'label': 'Art / Culture'},
}

BATCH_SIZE = 8  # questions per Groq call
SLEEP_BETWEEN_BATCHES = 1.2  # seconds


# ═══════════════════════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════════════════════

# Quiz ID line patterns (various formats in different files)
QUIZ_ID_PAT = re.compile(
    r'(?:#{1,3}\s*\*{0,2}|\*{0,2})'        # optional ##*/
    r'Quiz\s+([A-Z]{1,3}[\d][\d-]*)'       # "Quiz B1" or "Quiz SS1" etc.
    r'\s*(?:\(([^)]+)\))?'                 # optional "(Category)"
    r'\s*(?:\([^)]*énoncé[^)]*\))?'        # optional "(énoncé N)"
    r'\s*\*{0,2}',
    re.IGNORECASE,
)
TIMER_PAT    = re.compile(r'⏱️\s*(\d+)\s*s')
QUESTION_PAT = re.compile(r'\*\*Question\s*:\*\*\s*(.*)')
OPTION_PAT   = re.compile(r'^([A-D])\s*[).]\s+(.*)')
EXPL_PAT     = re.compile(r'\*\*Explicati(?:on|ons)\s*:\*\*')
CHAPTER_PAT  = re.compile(r'^#{1,4}\s+(.+)')


def _clean_opt(text: str) -> str:
    """Remove ✅, (Bonne réponse), trailing spaces/dots from option text."""
    text = re.sub(r'✅.*$', '', text)
    text = re.sub(r'\s+$', '', text)
    return text.strip()


def parse_quiz_markdown(text: str, default_category: str) -> list:
    """Parse one markdown quiz file → list of question dicts."""
    questions        = []
    current_category = default_category
    q                = None          # current question being built
    expl_lines       = []
    in_expl          = False
    pending_text     = []            # lines that might be the question text

    def flush(q, expl_lines, pending_text):
        """Finalise and save the current question if valid."""
        if q is None:
            return
        # If question text still missing, try to build from pending_text
        if not q['question'] and pending_text:
            q['question'] = ' '.join(pending_text).strip()
        # Build options list A→D
        q['options'] = [
            q['_opts'].get('A', ''),
            q['_opts'].get('B', ''),
            q['_opts'].get('C', ''),
            q['_opts'].get('D', ''),
        ]
        q['explanation'] = ' '.join(expl_lines).strip()
        del q['_opts']
        # Only keep questions with text AND 4 non-empty options
        if q['question'] and all(q['options']):
            questions.append(copy.deepcopy(q))

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # ── Separator ──────────────────────────────────────────────────────────
        if line.strip() == '---':
            flush(q, expl_lines, pending_text)
            q = None; expl_lines = []; in_expl = False; pending_text = []
            continue

        # ── Quiz ID line ────────────────────────────────────────────────────────
        qm = QUIZ_ID_PAT.search(line)
        if qm and re.search(r'\bQuiz\b', line, re.IGNORECASE):
            flush(q, expl_lines, pending_text)
            qid  = qm.group(1)
            qcat = qm.group(2).strip() if qm.group(2) else current_category
            q = {
                'id':            qid,
                'category':      qcat,
                'difficulty':    'moyen',
                'timer_seconds': 20,
                'question':      '',
                '_opts':         {},
                'correct':       'A',
                'explanation':   '',
            }
            expl_lines  = []
            in_expl     = False
            pending_text = []
            continue

        # ── No active question block — track chapter headings ──────────────────
        if q is None:
            cm = CHAPTER_PAT.match(line)
            if cm:
                heading = re.sub(r'[\*\[\]#]', '', cm.group(1)).strip()
                if heading and not re.search(r'\bQuiz\b', heading, re.IGNORECASE):
                    current_category = heading[:80]
            continue

        # ── Timer ───────────────────────────────────────────────────────────────
        tm = TIMER_PAT.search(line)
        if tm:
            q['timer_seconds'] = int(tm.group(1))
            continue

        # ── Question text ────────────────────────────────────────────────────────
        qpm = QUESTION_PAT.search(line)
        if qpm:
            q['question'] = qpm.group(1).strip()
            continue

        # ── Options ──────────────────────────────────────────────────────────────
        om = OPTION_PAT.match(line)
        if om and not in_expl:
            letter   = om.group(1).upper()
            opt_text = _clean_opt(om.group(2))
            if '✅' in om.group(2):
                q['correct'] = letter
            q['_opts'][letter] = opt_text
            continue

        # ── Explanation header ────────────────────────────────────────────────────
        if EXPL_PAT.search(line):
            in_expl = True
            continue

        # ── Explanation content ───────────────────────────────────────────────────
        if in_expl:
            stripped = line.strip()
            if stripped.startswith('-'):
                expl_lines.append(stripped.lstrip('-').strip())
            elif stripped:
                expl_lines.append(stripped)
            continue

        # ── Potential question text (no **Question:** header) ─────────────────────
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and not stripped.startswith('*') \
                and '⏱️' not in stripped and not OPTION_PAT.match(line):
            pending_text.append(stripped)

    # Save last question
    flush(q, expl_lines, pending_text)
    return questions


# ═══════════════════════════════════════════════════════════════════════════════
# SHUFFLE
# ═══════════════════════════════════════════════════════════════════════════════

def shuffle_options(questions: list) -> list:
    """Randomly shuffle options and update the correct letter."""
    LETTERS = ['A', 'B', 'C', 'D']
    for q in questions:
        opts   = q['options'][:]
        ci     = LETTERS.index(q['correct'].upper()) if q['correct'].upper() in LETTERS else 0
        answer = opts[ci] if ci < len(opts) else ''
        random.shuffle(opts)
        q['options'] = opts
        try:
            q['correct'] = LETTERS[opts.index(answer)]
        except ValueError:
            q['correct'] = 'A'
    return questions


def show_distribution(questions: list, label: str):
    dist = {'A': 0, 'B': 0, 'C': 0, 'D': 0}
    for q in questions:
        dist[q.get('correct', 'A')] = dist.get(q.get('correct', 'A'), 0) + 1
    total = len(questions)
    parts = ' | '.join(f"{k}={v}({v*100//total}%)" for k, v in dist.items())
    print(f"  [{label}] {parts}")


# ═══════════════════════════════════════════════════════════════════════════════
# GROQ: IMPROVE EXPLANATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def improve_batch(batch: list, subject_label: str) -> dict:
    """
    Send a batch of questions to Groq, get detailed educational explanations.
    Returns dict {"1": "expl...", "2": "expl...", ...}
    """
    LETTERS = ['A', 'B', 'C', 'D']
    parts = []
    for i, q in enumerate(batch, 1):
        ci = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(q['correct'].upper(), 0)
        correct_text = q['options'][ci] if ci < len(q['options']) else q['correct']
        opts_str = '\n'.join(
            f"  {LETTERS[j]}) {opt}" for j, opt in enumerate(q['options'])
        )
        parts.append(
            f"Question {i}: {q['question']}\nOptions:\n{opts_str}\n"
            f"Réponse correcte: {q['correct']}) {correct_text}\n"
            f"Explication actuelle (à améliorer): {q.get('explanation', '')}"
        )

    prompt = (
        f"Tu es un professeur expert en {subject_label} qui prépare des élèves haïtiens au BAC.\n\n"
        "Pour chaque question ci-dessous, rédige une explication PÉDAGOGIQUE et DÉTAILLÉE en français (3 à 5 phrases). "
        "Ton explication doit :\n"
        "1. Dire clairement pourquoi la réponse correcte est juste, en expliquant le concept/notion sous-jacent comme si tu l'enseignais\n"
        "2. Expliquer brièvement pourquoi chacune des autres options est incorrecte (sans utiliser les lettres A/B/C/D — utilise le texte des options)\n"
        "3. Être rédigée en prose fluide, comme une mini-leçon\n"
        "4. NE PAS commencer par 'La bonne réponse est...' — intègre la réponse dans l'explication\n\n"
        "Réponds UNIQUEMENT avec du JSON valide, rien d'autre :\n"
        '{"1": "explication détaillée...", "2": "explication...", ...}\n\n'
        + '\n\n---\n\n'.join(parts)
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.45,
            max_tokens=2500,
        )
        content = resp.choices[0].message.content.strip()
        # Extract JSON (sometimes wrapped in ```json ... ```)
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            return json.loads(json_match.group())
    except json.JSONDecodeError as e:
        print(f"    [JSON parse error] {e}")
    except Exception as e:
        print(f"    [Groq error] {e}")
    return {}


def improve_all_explanations(questions: list, subject_label: str) -> list:
    total   = len(questions)
    batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"  Improving {total} explanations ({batches} Groq batches)...")

    for bi in range(batches):
        start = bi * BATCH_SIZE
        batch = questions[start:start + BATCH_SIZE]
        result = improve_batch(batch, subject_label)

        for j, q in enumerate(batch):
            key = str(j + 1)
            if key in result and isinstance(result[key], str) and len(result[key]) > 20:
                q['explanation'] = result[key].strip()

        done = min(start + BATCH_SIZE, total)
        print(f"    [{done}/{total}] ✓")

        if bi < batches - 1:
            time.sleep(SLEEP_BETWEEN_BATCHES)

    return questions


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def process_all():
    for filename, info in FILES.items():
        filepath = os.path.join(DB_DIR, filename)
        if not os.path.exists(filepath):
            print(f"[SKIP] {filename} — file not found")
            continue

        print(f"\n{'='*60}")
        print(f"▶  {filename}  (subject={info['subject']})")

        with open(filepath, 'r', encoding='utf-8') as f:
            raw = f.read()

        # Skip if already converted to JSON format
        try:
            existing = json.loads(raw)
            if 'quiz' in existing and isinstance(existing['quiz'], list):
                print("  Already JSON format — checking if needs re-processing...")
                questions = existing['quiz']
                # Re-shuffle and re-improve
            else:
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            # Parse markdown
            questions = parse_quiz_markdown(raw, info['label'])

        print(f"  Parsed: {len(questions)} questions")
        if not questions:
            print("  [ERROR] No questions parsed — skipping")
            continue

        # Backup
        backup = filepath + '.backup'
        if not os.path.exists(backup):
            with open(backup, 'w', encoding='utf-8') as f:
                f.write(raw)
            print(f"  Backup → {os.path.basename(backup)}")

        # Shuffle
        show_distribution(questions, 'before shuffle')
        questions = shuffle_options(questions)
        show_distribution(questions, 'after shuffle')

        # Improve explanations via Groq
        questions = improve_all_explanations(questions, info['label'])

        # Build and save proper JSON
        output = {
            'matiere':     info['subject'],
            'total':       len(questions),
            'description': f"Quiz {info['label']} — BAC Haïti",
            'quiz':        questions,
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"  ✅ Saved {len(questions)} questions → {filename}")


if __name__ == '__main__':
    print("Starting quiz conversion + explanation improvement...")
    print(f"Files: {list(FILES.keys())}\n")
    process_all()
    print("\n✅ All done!")
