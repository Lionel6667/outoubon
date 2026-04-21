import json
from pathlib import Path

base = Path(r'C:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django\database\json')

for subject in ['maths', 'physique', 'chimie', 'svt', 'francais', 'philosophie']:
    path = base / f'exams_{subject}.json'
    if not path.exists():
        continue
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    exams = data.get('exams', [])
    rebuilt = [e for e in exams if e.get('rebuilt')]
    failed = [e for e in exams if e.get('rebuild_error')]
    print(f'\n[{subject.upper()}] Total={len(exams)} Rebuilt={len(rebuilt)} Failed={len(failed)}')
    for e in rebuilt[:2]:
        items = e.get('items', [])
        themes = [it.get('theme','?') for it in items]
        print(f'  OK: {e.get("file","?")} | {len(items)} items | themes={themes}')
    for e in failed[:1]:
        err = str(e.get('rebuild_error',''))[:120]
        print(f'  FAIL: {e.get("file","?")} | {err}')
