"""
Script pour générer des dissertations et questions texte supplémentaires pour la base histoire.
Cela augmente la variété des examens générés.
"""
import json
from pathlib import Path
import subprocess
import time
import sys

def generate_dissertations(count=15):
    """Génère des dissertations historiques."""
    prompt = f"""Génère exactement {count} dissertations d'histoire-géographie niveau BAC (Haïti/francophone).
Chaque dissertation doit être une question long forme (15-30 mots) basée sur des sujets historiques réels.
Utilise une variété de thèmes: Haïti, Colombie, Empire, Révolutions, Guerres, Mouvements sociaux, Géographie politique.

Format exact de réponse JSON (liste de {count} objets):
[
  {{
    "type": "dissertation",
    "theme": "Titre du thème",
    "enonce": "Question de dissertation complète",
    "difficulte": "moyen ou difficile",
    "source": "Bac synthétique {len(count)} - generated"
  }},
  ...
]

Sois strict: JSON valide uniquement, pas de texte avant/après.
"""
    
    try:
        response = call_gemini(prompt)
        items = json.loads(response)
        return items
    except Exception as e:
        print(f"Erreur génération dissertations: {e}")
        return []

def generate_question_texte(count=15):
    """Génère des questions texte (avec textes historiques)."""
    prompt = f"""Génère exactement {count} questions texte d'histoire-géographie niveau BAC.
Chaque item contient un texte historique court (100-300 mots) et une question sur le texte.
Varie les sujets: Haïti, Revolutions, Politique, Economie, Géographie.

Format exact de réponse JSON (liste de {count} objets):
[
  {{
    "type": "question_texte",
    "theme": "Titre du thème",
    "enonce": "Question sur le document (15-30 mots)",
    "difficulte": "moyen",
    "source": "Bac synthétique - generated",
    "texte": "Texte du document historique (100-300 mots)"
  }},
  ...
]

Sois strict: JSON valide uniquement, pas de texte avant/après.
"""
    
    try:
        response = call_gemini(prompt)
        items = json.loads(response)
        return items
    except Exception as e:
        print(f"Erreur génération questions texte: {e}")
        return []

def load_histoire_json():
    """Charge le fichier exams_histoire.json."""
    path = Path('database/json/exams_histoire.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_histoire_json(data):
    """Sauvegarde le fichier exams_histoire.json."""
    path = Path('database/json/exams_histoire.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    print("🔄 Chargement de exams_histoire.json...")
    history_data = load_histoire_json()
    
    # Les items sont dans le dernier exam généralement
    # ou créer une nouvelle catégorie "generated"
    
    print("\n📝 Génération de 15 dissertations...")
    dissertations = generate_dissertations(15)
    print(f"   ✅ {len(dissertations)} dissertations générées")
    
    time.sleep(2)  # Éviter les limites API
    
    print("\n📝 Génération de 15 questions texte...")
    questions_texte = generate_question_texte(15)
    print(f"   ✅ {len(questions_texte)} questions texte générées")
    
    # Ajouter à la dernière catégorie d'exams
    if history_data['exams']:
        last_exam = history_data['exams'][-1]
        if 'items' not in last_exam:
            last_exam['items'] = []
        
        last_exam['items'].extend(dissertations)
        last_exam['items'].extend(questions_texte)
        
        print(f"\n💾 Ajout de {len(dissertations) + len(questions_texte)} items au dernier exam")
        print(f"   Total items maintenant: {len(last_exam['items'])}")
    
    print("\n💾 Sauvegarde de exams_histoire.json...")
    save_histoire_json(history_data)
    
    print("\n✅ Génération complétée!")
    print(f"   Base agrandie: {len(dissertations)} dissertations + {len(questions_texte)} questions texte")

if __name__ == '__main__':
    main()
