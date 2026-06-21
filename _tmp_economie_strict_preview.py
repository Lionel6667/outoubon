import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
django.setup()

from core.gemini import generate_exam_from_db

exam = generate_exam_from_db('economie')
print('TITLE:', exam.get('title'))
print('PARTS:', len(exam.get('parts', [])))
print('TOTAL:', sum(sec.get('pts', 0) for p in exam.get('parts', []) for sec in p.get('sections', [])))
for pi, part in enumerate(exam.get('parts', []), 1):
    print(f"\n[{pi}] {part.get('label')}")
    for sec in part.get('sections', []):
        print('  -', sec.get('label'))
        for it in sec.get('items', []):
            txt = (it.get('text') or '').replace('\n', ' ')
            print('    *', txt[:190])
