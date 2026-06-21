"""Check all exercises for formula presence."""
import sys, os
sys.path.insert(0, r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
import django
django.setup()
from core.models import GeneratedCourseAsset

print("\n" + "="*70)
print("CHECKING ALL EXERCISES FOR FORMULAS")
print("="*70)

chapters = range(1, 12)
problem_count = 0

for ch_num in chapters:
    print(f"\nChapter {ch_num}:")
    for idx in range(3):
        section_id = f'physique_chapter_{ch_num}_exercise_{idx}'
        try:
            asset = GeneratedCourseAsset.objects.get(section_id=section_id)
            hints = asset.payload.get('hints', [])
            has_all_formulas = all('$' in str(h) for h in hints)
            status = "OK" if has_all_formulas else "NO"
            print(f"  [{status}] Exercise {idx}: {len(hints)} hints")
            
            if not has_all_formulas:
                problem_count += 1
                for i, h in enumerate(hints):
                    has_dollar = '$' in str(h) if h else False
                    symbol = "OK" if has_dollar else "NO"
                    preview = str(h)[:70] if h else "(empty)"
                    print(f"      [{symbol}] Hint {i}: {preview}")
        except GeneratedCourseAsset.DoesNotExist:
            print(f"  [!!] Exercise {idx}: NOT FOUND")
            problem_count += 1

print(f"\n" + "="*70)
total_ok = 33 - problem_count
print(f"SUMMARY: {total_ok}/33 OK - {problem_count} exercises missing formulas in hints")
print("="*70 + "\n")

