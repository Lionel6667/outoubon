"""
Script de finalisation: nettoyer les anciens chapitres et vérifier l'intégrité.
"""
import sys, os
sys.path.insert(0, r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')

import django
django.setup()

from core.models import SubjectChapter, GeneratedCourseAsset

print("\n[FINALISATION] Nettoyage du cours de physique")
print("=" * 70)

# 1. Identifier les chapitres du nouveau cours (ceux avec les titres du fichier)
NEW_CHAPTER_TITLES = [
    'LE CONDENSATEUR',
    'LE SOLÉNOÏDE ET L\'INDUCTANCE',
    'INDUCTION ÉLECTROMAGNÉTIQUE',
    'FORCE DE LAPLACE ET GALVANOMÈTRE',
    'COURANT ALTERNATIF SINUSOÏDAL',
    'CINÉMATIQUE — MOUVEMENT RECTILIGNE',
    'MOUVEMENT DE PROJECTILE (BALISTIQUE)',
    'PENDULE SIMPLE ET OSCILLATIONS',
    'ONDES',
    'COMMENT RÉPONDRE AUX QUESTIONS DU BAC',
    'RÉSUMÉ DES FORMULES ESSENTIELLES',
]

# 2. Lister les chapitres actuels
all_physique = SubjectChapter.objects.filter(subject='physique').order_by('order')
print(f"\nChapitres existants: {all_physique.count()}")

# Identifier les nouveaux vs anciens
new_chapters = []
old_chapters = []

for ch in all_physique:
    # C'est un nouveau chapitre si le titre contient un des titres du nouveau cours
    is_new = any(new_title in ch.title for new_title in NEW_CHAPTER_TITLES)
    
    if is_new:
        new_chapters.append(ch)
    else:
        old_chapters.append(ch)

print(f"\n  Nouveaux chapitres: {len(new_chapters)}")
for ch in new_chapters:
    print(f"    ✓ {ch.title}")

print(f"\n  Anciens chapitres: {len(old_chapters)}")
for ch in old_chapters:
    print(f"    - {ch.title}")

# 3. Supprimer les anciens chapitres (ils ont des sessions associées à priori NULL)
if old_chapters:
    print(f"\n[ACTION] Suppression des {len(old_chapters)} anciens chapitres...")
    for ch in old_chapters:
        ch.delete()
    print(f"  ✓ {len(old_chapters)} anciens chapitres supprimés")

# 4. Vérifier l'intégrité des exercices
print(f"\n[CHECK] Vérification des exercices...")
exercises = GeneratedCourseAsset.objects.filter(
    course_key='physique_chapters',
    asset_type='exercise_bank'
)

print(f"  Exercices: {exercises.count()}")
print(f"  Attent: 33 (11 chapitres × 3)")

if exercises.count() == 33:
    print(f"  ✓ Nombre d'exercices correct!")
    
    # Vérifier que chaque exercice a des hints avec formules
    invalid_exercises = 0
    for ex in exercises:
        payload = ex.payload
        hints = payload.get('hints', [])
        has_formulas = all('$' in h for h in hints) if hints else False
        if not has_formulas:
            invalid_exercises += 1
            print(f"    ⚠ {ex.section_id}: hints sans formules")
    
    if invalid_exercises == 0:
        print(f"  ✓ Tous les exercices ont des formules dans les hints!")
else:
    print(f"  ⚠ Nombre d'exercices incorrect: {exercises.count()} au lieu de 33")

# 5. Résumé final
print(f"\n" + "=" * 70)
print(f"[RESUME FINAL]")
remaining_chapters = SubjectChapter.objects.filter(subject='physique').count()
print(f"  Chapitres physique: {remaining_chapters}")
print(f"  Exercices: {exercises.count()}")
print(f"  Status: ", end="")

if remaining_chapters == 11 and exercises.count() == 33:
    print("✓ PRÊT POUR UTILISATION")
else:
    print(f"⚠ À VÉRIFIER")

print("=" * 70 + "\n")
