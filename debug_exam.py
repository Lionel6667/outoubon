import os, sys, django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
sys.path.insert(0, r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django')
django.setup()

from core import pdf_loader as pl
from core.gemini import generate_structured_exam

text = pl.get_exam_context('maths', max_chars=5000)
print("=== TEST generate_structured_exam (maths) ===")
result = generate_structured_exam(text[:2000], 'maths')
print("Result keys:", list(result.keys()) if result else "EMPTY")
if result:
    parts = result.get('parts', [])
    for p in parts:
        print(f"  Part: {p.get('label','?')}")
        for s in p.get('sections', []):
            print(f"    Section: {s.get('id','?')} type={s.get('type','?')} items={len(s.get('items',[]))}")


