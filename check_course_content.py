import sys, os
sys.path.insert(0, r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
import django
django.setup()

from core.models import SubjectChapter

chapters = SubjectChapter.objects.filter(subject='physique').order_by('order')
print("\n" + "="*70)
print("ETAT ACTUEL DU COURS PHYSIQUE")
print("="*70)

for ch in chapters:
    print(f"\nChapitre {ch.order}: {ch.title}")
    if hasattr(ch, 'content') and ch.content:
        lines = ch.content.split('\n')[:3]
        print(f"  [Content] {len(lines)} lignes commençant par:")
        for line in lines[:2]:
            print(f"    {line[:70]}")
    else:
        desc_preview = ch.description[:100] if ch.description else "(empty)"
        print(f"  [Description] {desc_preview}...")
