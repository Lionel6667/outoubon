"""
Import vrais exercices du BAC depuis le JSON vers la base de données.
100% original - pas d'exercices générés par l'IA.
"""
import json
import sys
import os
sys.path.insert(0, r'c:\Users\LE SANG DE JESUS\OneDrive\Desktop\project coding\BacIA_Django')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bacia.settings')

import django
django.setup()

from core.models import SubjectChapter, BACExercise

# Mapping de thèmes aux chapitres
THEME_TO_CHAPTER = {
    'condensateur': 'LE CONDENSATEUR',
    'solenoid': 'LE SOLÉNOÏDE ET L\'INDUCTANCE',
    'bobine': 'LE SOLÉNOÏDE ET L\'INDUCTANCE',
    'inductance': 'LE SOLÉNOÏDE ET L\'INDUCTANCE',
    'induction': 'INDUCTION ÉLECTROMAGNÉTIQUE',
    'laplace': 'FORCE DE LAPLACE ET GALVANOMÈTRE',
    'courant': 'COURANT ALTERNATIF SINUSOÏDAL',
    'oscillation': 'PENDULE SIMPLE ET OSCILLATIONS',
    'pendule': 'PENDULE SIMPLE ET OSCILLATIONS',
    'onde': 'ONDES',
    'projectile': 'MOUVEMENT DE PROJECTILE (BALISTIQUE)',
    'balistique': 'MOUVEMENT DE PROJECTILE (BALISTIQUE)',
    'cinématique': 'CINÉMATIQUE — MOUVEMENT RECTILIGNE',
    'mouvement': 'CINÉMATIQUE — MOUVEMENT RECTILIGNE',
}

def get_chapter_from_content(content, filename=''):
    """Analyse le contenu du texte pour déterminer le chapitre."""
    
    # D'abord vérifier le nom du fichier
    filename_lower = filename.lower()
    for theme_keyword, chapter_title in THEME_TO_CHAPTER.items():
        if theme_keyword in filename_lower:
            return chapter_title
    
    # Si le nom ne correspond pas, analyser le contenu
    content_lower = content.lower()[:2000]  # Premières 2000 caractères
    
    # Mots clés de contenu pour chaque chapitre - plus spécifiques
    chapter_keywords = {
        'LE CONDENSATEUR': ['condensateur', 'capacitance', 'champ électrique', 'diélectrique'],
        'LE SOLÉNOÏDE ET L\'INDUCTANCE': ['solénoïde', 'inductance', 'bobine', 'spire', 'auto-induction'],
        'INDUCTION ÉLECTROMAGNÉTIQUE': ['induction', 'faraday', 'lenz', 'flux magnétique'],
        'FORCE DE LAPLACE ET GALVANOMÈTRE': ['laplace', 'galvanométr', 'force magnétique', 'ampère'],
        'COURANT ALTERNATIF SINUSOÏDAL': ['courant alternatif', 'alternatif', 'sinusoïdal', 'impédance', 'résonance', 'ac'],
        'CINÉMATIQUE — MOUVEMENT RECTILIGNE': ['cinématique', 'vecteur position', 'vecteur vitesse', 'accélération'],
        'MOUVEMENT DE PROJECTILE (BALISTIQUE)': ['projectile', 'balistique', 'lancer', 'trajectoire parabolique'],
        'PENDULE SIMPLE ET OSCILLATIONS': ['pendule simple', 'oscillation', 'période', 'fréquence'],
        'ONDES': ['onde mécanique', 'onde sonore', 'diffraction', 'longueur d\'onde'],
    }
    
    # Compter les matches
    best_chapter = None
    best_count = 0
    
    for chapter_title, keywords in chapter_keywords.items():
        match_count = sum(1 for kw in keywords if kw in content_lower)
        if match_count > best_count:
            best_count = match_count
            best_chapter = chapter_title
    
    return best_chapter if best_count > 0 else None

def import_bac_exercises():
    """Importe les vrais exercices du BAC depuis le JSON."""
    
    print("\n" + "="*70)
    print("IMPORT DES VRAIS EXERCICES DU BAC")
    print("="*70)
    
    # Charger le JSON
    with open('database/json/exams_physique.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Nettoyer les exercices existants
    initial_count = BACExercise.objects.count()
    print(f"\n[1] État initial: {initial_count} exercices existants")
    
    if initial_count > 0:
        BACExercise.objects.all().delete()
        print(f"    ✓ {initial_count} exercices supprimés")
    
    # Traiter chaque examen
    imported_count = 0
    chapters_used = set()
    unmapped_files = []
    
    for exam in data['exams']:
        exam_file = exam['file']
        exam_year = exam.get('year', '0000')
        exam_series = ', '.join(exam.get('series', []))
        exam_text = exam.get('text', '')
        
        # Déterminer le chapitre
        chapter_title = get_chapter_from_content(exam_text, exam_file)
        
        if not chapter_title:
            unmapped_files.append(exam_file)
            continue
        
        # Récupérer le chapitre
        try:
            chapter = SubjectChapter.objects.get(subject='physique', title=chapter_title)
        except SubjectChapter.DoesNotExist:
            print(f"  [!] Chapitre '{chapter_title}' non trouvé pour {exam_file}")
            continue
        
        # Créer l'exercice (l'examen complet est le problème)
        if exam_text:
            exercise = BACExercise.objects.create(
                chapter=chapter,
                exam_file=exam_file,
                exam_year=exam_year,
                exam_series=exam_series,
                problem_number=1,
                title=f"Examen {exam_year}",
                content=exam_text,
                theme=chapter_title,
                points=0,
            )
            imported_count += 1
            chapters_used.add(chapter_title)
    
    # Rapport final
    print(f"\n[2] Import terminé:")
    print(f"    ✓ {imported_count} exercices importés")
    print(f"    ✓ {len(chapters_used)} chapitres couverts")
    
    if unmapped_files:
        print(f"    [!] {len(unmapped_files)} fichiers sans mapping:")
        for f in unmapped_files[:5]:
            print(f"        - {f}")
        if len(unmapped_files) > 5:
            print(f"        ... et {len(unmapped_files) - 5} autres")
    
    print("\n" + "="*70 + "\n")

if __name__ == '__main__':
    import_bac_exercises()
