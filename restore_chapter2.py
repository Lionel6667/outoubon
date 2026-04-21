"""Restore missing chapter 2 with exercises."""
import sys, os
sys.path.insert(0, r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')
import django
django.setup()

from core.models import SubjectChapter, GeneratedCourseAsset
from core.exercise_generator import generate_physics_exercise

# Create missing chapter 2
chapter, created = SubjectChapter.objects.update_or_create(
    subject='physique',
    subsection='',
    title='LE SOLÉNOÏDE ET L\'INDUCTANCE',
    defaults={
        'description': 'Électromagnétisme: études des champs magnétiques créés par les solénoïdes et l\'inductance.',
        'order': 2,
        'exam_excerpts': '[]',
    }
)
status = 'CREATED' if created else 'EXISTANT'
print(f'Chapitre: {status} - {chapter.title}')

# Create 3 exercises
for idx in range(3):
    exercise = generate_physics_exercise(
        section_title='LE SOLÉNOÏDE ET L\'INDUCTANCE',
        section_id='physique_ch2',
        index=idx
    )
    
    asset_key = f'physique_chapter_2_exercise_{idx}'
    
    asset, created = GeneratedCourseAsset.objects.update_or_create(
        course_key='physique_chapters',
        section_id=asset_key,
        asset_type='exercise_bank',
        defaults={
            'section_title': chapter.title,
            'payload': exercise,
        }
    )
    
    print(f'  Exercise {idx + 1}: {exercise.get("title", "Sans titre")}')

print('\nVerifying hints:')
for idx in range(3):
    asset = GeneratedCourseAsset.objects.get(section_id=f'physique_chapter_2_exercise_{idx}')
    hints = asset.payload.get('hints', [])
    has_formulas = all('$' in str(h) for h in hints)
    print(f'  Exercise {idx}: has_formulas={has_formulas}')
    for i, h in enumerate(hints):
        print(f'    Hint {i+1}: {h[:80]}...')
