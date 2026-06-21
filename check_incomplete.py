import json
from pathlib import Path

data = json.loads(Path('database/json/exams_maths.json').read_text(encoding='utf-8'))
for exam in data.get('exams', []):
    yr = exam.get('year', '?')
    nm = exam.get('name', '?')
    for item in exam.get('items', []):
        qs = item.get('questions') or []
        t = item.get('type', '')
        intro = (item.get('intro') or item.get('enonce') or '')
        if t == 'exercice' and len(qs) <= 2:
            print('yr=%s nm=%s theme=%s' % (yr, nm, item.get('theme','')))
            print('  intro:', repr(intro[:120]))
            print('  nb questions:', len(qs))
            for q in qs:
                print('    -', repr(str(q)[:100]))
            print()
