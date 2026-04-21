"""
Fix explanations in quiz_kreyol.json that reference letters A/B/C/D
(which became wrong after randomizing option order).
Uses Groq to rewrite only those 10 explanations.
"""
import json, os, django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
django.setup()
from django.conf import settings
from groq import Groq

client = Groq(api_key=settings.GROQ_API_KEY)
MODEL = 'openai/gpt-oss-120b'

path = 'database/quiz_kreyol.json'
data = json.loads(open(path, encoding='utf-8').read())
qs = data['quiz']
ids_to_fix = [3, 36, 37, 38, 48, 52, 103, 133, 143, 144]
letters = ['A', 'B', 'C', 'D']

for qid in ids_to_fix:
    q = next(x for x in qs if x['id'] == qid)
    opts_text = '\n'.join(f'{letters[i]}: {opt}' for i, opt in enumerate(q['options']))
    correct_letter = q['correct']
    correct_text = q['options'][letters.index(correct_letter)]

    prompt = (
        'Ou se yon pwofesè kreyòl ayisyen ki ekri eksplikasyon pou kesyon egzamen Bac.\n\n'
        f'KESYON: {q["question"]}\n'
        f'OPSYON:\n{opts_text}\n'
        f'REPONSE KÒRÈK: {correct_letter}) {correct_text}\n\n'
        'Ekri yon eksplikasyon ki:\n'
        '1. Eksplike POUKISA reponse kòrèk la kòrèk (site tèks li dirèkteman)\n'
        '2. Eksplike rapid poukisa lòt opsyon yo pa kòrèk (site tèks yo dirèkteman, PAS lèt A/B/C/D)\n'
        '3. ENPO TAN: pa janm itilize lèt A, B, C, D nan eksplikasyon — toujou site tèks opsyon an dirèkteman\n'
        '4. Ekri an kreyòl ayisyen, maks 3 fraz\n'
        'Eksplikasyon sèlman, san lòt tèks:'
    )

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=300,
    )
    new_expl = resp.choices[0].message.content.strip()
    print(f'ID {qid} [{correct_letter}]: {new_expl[:120]}...')
    q['explanation'] = new_expl

with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print('\nDone — explanations updated and saved.')
