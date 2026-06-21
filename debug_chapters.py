import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'bacia.settings'
import django
django.setup()

from core import exo_loader
exos = exo_loader.get_exercises('physique', chapter='', n=30)
print(f"Total exos: {len(exos)}\n")

# Check chapter values
chapters_demo_count = 0
chapters_other = []
for ex in exos:
    ch = ex.get('chapter', 'N/A')
    if 'Démonstrations' in str(ch):
        chapters_demo_count += 1
    else:
        if ch not in chapters_other:
            chapters_other.append(ch)

print(f"Démonstrations count: {chapters_demo_count}")  
print(f"Other chapters: {chapters_other[:5]}")
print(f"\nFirst 5 exercises:")
for ex in exos[:5]:
    print(f"  Chapter: {repr(ex.get('chapter'))}")
