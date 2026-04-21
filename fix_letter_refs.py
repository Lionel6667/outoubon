#!/usr/bin/env python3
"""
fix_letter_refs.py
~~~~~~~~~~~~~~~~~~
Scan all converted quiz JSON files for explanations that still reference
letters A / B / C / D (which become wrong after serve-time shuffle).
Rewrite those explanations via Groq to use the actual option text instead.

Run AFTER convert_quizzes.py has finished.
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
import django; django.setup()
from django.conf import settings
from groq import Groq

client = Groq(api_key=settings.GROQ_API_KEY)
MODEL  = 'openai/gpt-oss-120b'

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database')

FILES = [
    'quiz_SVT.json',
    'quiz_sc_social.json',
    'quiz_physique.json',
    'quiz_philosophie.json',
    'quiz_informatique.json',
    'quiz_economie.json',
    'quiz_chimie.json',
    'quiz_art.json',
    'quiz_kreyol.json',
]

# Patterns that indicate a letter reference (A, B, C or D alone)
LETTER_REF = re.compile(
    r'(?<!\w)'                     # not preceded by any Unicode word char (incl. accented)
    r'[ABCD]'
    r'(?=\s*[:).\-–])'            # followed by : ) . - –
    r'|'
    r'\b(?:option|réponse|réponse correcte|bonne réponse|choix)\s+[ABCD]\b'
    r'|'
    r'\([ABCD]\)',                 # (A) (B) etc.
    re.IGNORECASE
)

BATCH_SIZE  = 6
SLEEP_SECS  = 1.2


def needs_fix(expl: str) -> bool:
    return bool(LETTER_REF.search(expl or ''))


def fix_batch(batch: list, subject: str, filepath: str) -> dict:
    """
    batch: list of dicts with keys: index, question, options, correct_idx, explanation
    Returns {index: new_explanation}
    """
    LETTERS = ['A', 'B', 'C', 'D']
    parts = []
    for i, item in enumerate(batch, 1):
        opts_str = '\n'.join(
            f"  {LETTERS[j]}) {opt}" for j, opt in enumerate(item['options'])
        )
        correct_text = item['options'][item['correct_idx']] if item['correct_idx'] < len(item['options']) else ''
        parts.append(
            f"Question {i}: {item['question']}\n"
            f"Options:\n{opts_str}\n"
            f"Réponse correcte: {correct_text}\n"
            f"Explication à réécrire: {item['explanation']}"
        )

    prompt = (
        f"Tu es un professeur expert qui prépare des élèves haïtiens au BAC ({subject}).\n\n"
        "CONSIGNE ABSOLUE : réécris chaque explication en prose pédagogique (3-5 phrases). "
        "Tu NE DOIS JAMAIS utiliser les lettres A, B, C ou D pour désigner les options. "
        "Référence toujours les options par leur texte réel. "
        "Explique pourquoi la réponse correcte est juste (leçon mini), et pourquoi les autres sont incorrectes.\n\n"
        "Réponds UNIQUEMENT avec du JSON valide, pas de texte avant/après :\n"
        '{"1": "explication...", "2": "explication...", ...}\n\n'
        + '\n\n---\n\n'.join(parts)
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.4,
            max_tokens=2000,
        )
        content = resp.choices[0].message.content.strip()
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            return json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"    [JSON parse error] {e}")
    except Exception as e:
        print(f"    [Groq error] {e}")
    return {}


def process_file(filename: str):
    filepath = os.path.join(DB_DIR, filename)
    if not os.path.exists(filepath):
        print(f"[SKIP] {filename} — not found")
        return

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    questions = data.get('quiz', [])
    if not questions:
        # Maybe it's a flat list
        if isinstance(data, list):
            questions = data
        else:
            print(f"  [SKIP] {filename} — no 'quiz' key")
            return

    subject = data.get('matiere', filename.replace('quiz_', '').replace('.json', ''))

    # Find problematic questions
    bad_indices = []
    for i, q in enumerate(questions):
        expl = q.get('explanation', q.get('explication', ''))
        if needs_fix(expl):
            bad_indices.append(i)

    if not bad_indices:
        print(f"  ✅ {filename} — no letter refs found")
        return

    print(f"  ⚠️  {filename} — {len(bad_indices)} explanations with letter refs → fixing...")

    # Build items to send
    items_to_fix = []
    for i in bad_indices:
        q = questions[i]
        opts = q.get('options', [])
        correct_letter = q.get('correct', 'A').upper()
        correct_idx = {'A':0,'B':1,'C':2,'D':3}.get(correct_letter, 0)
        items_to_fix.append({
            'q_index':     i,
            'question':    q.get('question', q.get('enonce', '')),
            'options':     opts,
            'correct_idx': correct_idx,
            'explanation': q.get('explanation', q.get('explication', '')),
        })

    # Process in batches
    batches = (len(items_to_fix) + BATCH_SIZE - 1) // BATCH_SIZE
    fixed_count = 0

    for bi in range(batches):
        start = bi * BATCH_SIZE
        batch = items_to_fix[start:start + BATCH_SIZE]
        result = fix_batch(batch, subject, filepath)

        for j, item in enumerate(batch):
            key = str(j + 1)
            if key in result and isinstance(result[key], str) and len(result[key]) > 20:
                q = questions[item['q_index']]
                if 'explanation' in q:
                    q['explanation'] = result[key].strip()
                elif 'explication' in q:
                    q['explication'] = result[key].strip()
                else:
                    q['explanation'] = result[key].strip()
                fixed_count += 1

        done = min(start + BATCH_SIZE, len(items_to_fix))
        print(f"    [{done}/{len(items_to_fix)}] ✓")

        if bi < batches - 1:
            time.sleep(SLEEP_SECS)

    # Save back
    if isinstance(data, list):
        out = questions
    else:
        data['quiz'] = questions
        out = data

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # Verify: check how many still have letter refs
    still_bad = sum(1 for q in questions if needs_fix(q.get('explanation', q.get('explication', ''))))
    print(f"  ✅ {filename} — fixed {fixed_count}/{len(bad_indices)} | still bad: {still_bad}")


def main():
    print("=== Fix letter references in quiz explanations ===\n")
    for filename in FILES:
        print(f"\n{'='*55}")
        print(f"▶  {filename}")
        process_file(filename)
    print("\n\n✅ All done!")


if __name__ == '__main__':
    main()
