import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
import django
django.setup()
from core.pdf_loader import get_chapters_from_json
from core.views import _get_cours_chapters

print("=== from get_chapters_from_json('philosophie') ===")
chapters = get_chapters_from_json('philosophie')
for c in chapters:
    print(f"  num={c.get('num')} title={c.get('title')}")

print()
print("=== from _get_cours_chapters('philosophie') ===")
try:
    chapters2 = _get_cours_chapters('philosophie')
    for c in chapters2:
        print(f"  num={c.get('num')} title={c.get('title')}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== from get_chapters_from_json('physique') ===")
chapters3 = get_chapters_from_json('physique')
for c in chapters3:
    print(f"  num={c.get('num')} title={c.get('title')}")
