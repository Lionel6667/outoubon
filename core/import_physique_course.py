"""
Script pour importer le nouveau cours de physique et créer les exercices intelligents.

Usage:
    python manage.py shell < import_physique.py
    
Ou:
    python -c "import json; exec(open('core/import_physique.py').read())"
"""

import json
import re
from pathlib import Path

# Setup Django
import sys, os
sys.path.insert(0, r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')

import django
django.setup()

from core.models import SubjectChapter, GeneratedCourseAsset
from core.exercise_generator import generate_physics_exercise

def read_course_file(filepath):
    """Lire le fichier de cours et retourner le texte."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


def extract_chapters(course_text):
    """Extract chapters from markdown course text.
    
    Returns: List of dicts with {title, content, order}
    """
    # Pattern pour ## CHAPITRE X : Titre
    chapter_pattern = r'^##\s+CHAPITRE\s+(\d+)\s*:\s*(.+?)$'
    
    chapters = []
    matches = list(re.finditer(chapter_pattern, course_text, re.MULTILINE | re.IGNORECASE))
    
    if not matches:
        print("[WARNING] No chapters found with ## CHAPITRE X : pattern")
        return []
    
    for i, match in enumerate(matches):
        chapter_num = int(match.group(1))
        title = match.group(2).strip()
        
        # Contenu du chapitre = du début de ce chapitre au début du suivant
        start_pos = match.start()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(course_text)
        content = course_text[start_pos:end_pos]
        
        chapters.append({
            'num': chapter_num,
            'title': title,
            'content': content,
        })
    
    return chapters


def create_chapter_description(chapter_content):
    """Extract a 2-3 sentence description from chapter content."""
    # Prendre les premières 300 caractères après le titre, sans les markdown
    lines = chapter_content.split('\n')
    description_lines = []
    
    for line in lines[1:]:  # Skip the title line
        if line.strip() and not line.startswith('#'):
            # Remove markdown formatting
            clean_line = re.sub(r'[*_`]', '', line).strip()
            if clean_line:
                description_lines.append(clean_line)
                if len(' '.join(description_lines)) > 200:
                    break
    
    return ' '.join(description_lines)[:300]


def find_relevant_exams_for_chapter(chapter_title, chapter_content):
    """
    Heuristic function to find which exams correspond to this chapter.
    Returns a list of exam filenames that would be relevant.
    
    Par exemple:
    - "Capaciteurs" → examens sur les condensateurs
    - "Solénoïde" → examens sur l'induction
    - "Mouvement" → examens de mécanique
    """
    # Keywords mapping: chapter_keyword -> exam_keywords
    KEYWORDS = {
        'capaciteur': ['capacitor', 'condensateur', 'dielectric'],
        'condensateur': ['capacitor', 'condensateur', 'dielectric'],
        'solénoïde': ['solenoid', 'bobine', 'induction', 'inductance'],
        'induction': ['induction', 'faraday', 'lenz', 'flux'],
        'champ magnétique': ['magnetic field', 'champ', 'aimant'],
        'courant alternatif': ['alternating', 'alternatif', 'impedance', 'résonance'],
        'force de laplace': ['laplace', 'force', 'moteur'],
        'déphasage': ['phase', 'déphasage', 'reactance'],
        'mouvement': ['kinematics', 'projectile', 'chute', 'mouvement'],
        'oscillations': ['oscillator', 'pendule', 'harmonic'],
        'ondes': ['wave', 'onde', 'diffraction'],
    }
    
    chapter_lower = chapter_title.lower()
    relevant_keywords = []
    
    for keyword_group, exam_keywords in KEYWORDS.items():
        if keyword_group in chapter_lower:
            relevant_keywords.extend(exam_keywords)
    
    return relevant_keywords


def import_chapter(chapter_data, order):
    """
    Create or update a SubjectChapter from chapter data extracted from course.
    
    Returns: SubjectChapter instance
    """
    title = chapter_data['title']
    description = create_chapter_description(chapter_data['content'])
    exam_keywords = find_relevant_exams_for_chapter(title, chapter_data['content'])
    
    chapter, created = SubjectChapter.objects.update_or_create(
        subject='physique',
        subsection='',
        title=title,
        defaults={
            'description': description,
            'order': order,
            'exam_excerpts': json.dumps(exam_keywords),
        }
    )
    
    status = "CREATED" if created else "UPDATED"
    print(f"  [{status}] Ch {order}: {title}")
    
    return chapter


def create_chapter_exercises(chapter, order):
    """
    Create 3 relevant exercises for this chapter using our formula_utils.
    Intelligently map exam content to chapter.
    
    Uses generate_physics_exercise() with deterministic seeding based on chapter_num.
    """
    # Create 3 exercises for this chapter
    for idx in range(3):
        # Generate using our formula system
        exercise = generate_physics_exercise(
            section_title=chapter.title,
            section_id=f"physique_ch{order}",
            index=idx
        )
        
        # Store in GeneratedCourseAsset for caching
        asset_key = f"physique_chapter_{order}_exercise_{idx}"
        
        asset, created = GeneratedCourseAsset.objects.update_or_create(
            course_key='physique_chapters',
            section_id=asset_key,
            asset_type='exercise_bank',
            defaults={
                'section_title': chapter.title,
                'payload': exercise,
            }
        )
        
        print(f"    • Exercise {idx + 1}: {exercise.get('title', 'Sans titre')}")


def clear_old_physics_content():
    """
    Clear old physics course assets before importing new ones.
    """
    print("[CLEANUP] Suppression des anciens exercices catchés...")
    
    count = GeneratedCourseAsset.objects.filter(
        course_key__startswith='physique'
    ).count()
    
    GeneratedCourseAsset.objects.filter(
        course_key__startswith='physique'
    ).delete()
    
    print(f"  ✓ {count} anciens assets supprimés")


def main():
    """Main import routine."""
    print("\n" + "="*70)
    print("IMPORTATION DU NOUVEAU COURS DE PHYSIQUE")
    print("="*70 + "\n")
    
    # Paths
    course_file = Path(r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django\database\note_physique.json')
    
    if not course_file.exists():
        print(f"[ERROR] Course file not found: {course_file}")
        return False
    
    # 1. Read course
    print("[1] Lecture du fichier de cours...")
    course_text = read_course_file(course_file)
    print(f"  ✓ Fichier chargé ({len(course_text)} caractères)")
    
    # 2. Extract chapters
    print("\n[2] Extraction des chapitres...")
    chapters = extract_chapters(course_text)
    print(f"  ✓ {len(chapters)} chapitres trouvés")
    
    if not chapters:
        print("[ERROR] Aucun chapitre n'a pu être extrait!")
        return False
    
    # 3. Clear old content
    print("\n[3] Nettoyage des anciens contenus...")
    clear_old_physics_content()
    
    # 4. Import chapters and create exercises
    print("\n[4] Importation des chapitres et création des exercices...")
    
    for order, chapter_data in enumerate(chapters, start=1):
        print(f"\n  Chapitre {order}: {chapter_data['title']}")
        
        # Import chapter
        chapter_obj = import_chapter(chapter_data, order)
        
        # Create 3 exercises per chapter
        create_chapter_exercises(chapter_obj, order)
    
    print("\n" + "="*70)
    print("IMPORTATION TERMINÉE!")
    print("="*70 + "\n")
    
    return True


if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
