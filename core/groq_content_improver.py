"""
Service to improve course content using Groq API
- Validates chapter pedagogy and content quality
- Generates quiz questions in the correct format
"""

import json
import re
from django.conf import settings
from groq import Groq

# Initialize Groq with API key from Django settings
groq_api_key = getattr(settings, 'GROQ_API_KEY', '')
client = Groq(api_key=groq_api_key) if groq_api_key else None


def extract_existing_quizzes(chapter_data: dict) -> list:
    """
    Extract existing quizzes from chapter data.
    Checks all fields matching pattern quiz*, and validates format.
    Returns list of valid quiz objects, or empty list if none found.
    """
    existing_quizzes = []
    
    for key, value in chapter_data.items():
        # Check if field is a quiz field (name contains 'quiz')
        if 'quiz' not in key.lower():
            continue
        
        # If it's already a list, check if it's valid quizzes
        if isinstance(value, list) and len(value) > 0:
            # Check if first item is a valid quiz object
            if isinstance(value[0], dict):
                if 'question' in value[0] and 'options' in value[0] and 'reponse' in value[0]:
                    # Valid quiz array - validate all items
                    for quiz in value:
                        if is_valid_quiz_object(quiz):
                            existing_quizzes.append(quiz)
        
        # If it's a string, it might be raw text quiz data
        elif isinstance(value, str) and len(value) > 50:
            # Try to parse it as JSON first
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    for item in parsed:
                        if is_valid_quiz_object(item):
                            existing_quizzes.append(item)
                elif isinstance(parsed, dict) and is_valid_quiz_object(parsed):
                    existing_quizzes.append(parsed)
            except:
                # Not JSON - skip text format quizzes (they can't be rendered interactively)
                pass
    
    return existing_quizzes


def is_valid_quiz_object(obj: dict) -> bool:
    """Check if object has valid quiz structure."""
    if not isinstance(obj, dict):
        return False
    
    required = ['question', 'options', 'reponse']
    if not all(key in obj for key in required):
        return False
    
    # Validate options is list with strings
    if not isinstance(obj.get('options'), list) or len(obj.get('options', [])) < 2:
        return False
    
    # Validate reponse is valid (A, B, C, D)
    reponse = str(obj.get('reponse', '')).upper()
    if not reponse in ['A', 'B', 'C', 'D']:
        return False
    
    return True

def generate_quizzes_for_chapter(chapter_data: dict, chapter_title: str, matiere: str) -> list:
    """
    Get quizzes for chapter. FIRST checks if valid quizzes already exist.
    If found, returns them. Otherwise generates new ones using Groq.
    Format: [{"niveau": "Facile|Moyen|Difficile", "question": "...", "options": [...], "reponse": "A", "explication": "..."}]
    """
    
    # STEP 1: Check if chapter already has valid quizzes
    existing_quizzes = extract_existing_quizzes(chapter_data)
    if len(existing_quizzes) > 0:
        return existing_quizzes
    
    # STEP 2: No valid quizzes found - generate new ones
    # Build chapter context
    chapter_text = f"Titre: {chapter_title}\n"
    
    # Extract main content
    for key, value in chapter_data.items():
        if key in ['titre', 'id', 'introduction', 'exercices_type_bac', 'quiz_chapitre', 'quiz']:
            continue
        
        if isinstance(value, str) and len(value) < 500:
            chapter_text += f"{key}: {value}\n"
        elif isinstance(value, list) and value and isinstance(value[0], str):
            chapter_text += f"{key}: {', '.join(value[:5])}\n"
    
    prompt = f"""Tu es un expert pédagogue spécialisé en création de questions d'examen pour le Baccalauréat Haïtien.

Matière: {matiere}
Chapitre: {chapter_title}

Contenu du chapitre:
{chapter_text}

Tache: Génère exactement 5 questions d'examen type (QCM) pour ce chapitre qui testent des concepts clés. Les questions doivent être pedagogiquement pertinentes.

Format EXACT (JSON array):
[
  {{
    "niveau": "Facile",
    "question": "Question claire et précise",
    "options": ["A. Option 1", "B. Option 2", "C. Option 3", "D. Option 4"],
    "reponse": "A",
    "explication": "Explication détaillée pourquoi c'est la bonne réponse"
  }},
  ...
]

Distribue les niveaux: 2 Facile, 2 Moyen, 1 Difficile.
Les options doivent être plausibles mais la bonne réponse doit être clairement correcte.
Retourne SEULEMENT le JSON, sans texte supplémentaire."""

    try:
        if not client:
            return []
        
        message = client.messages.create(
            model="mixtral-8x7b-32768",
            max_tokens=2000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].text.strip()
        
        # Extract JSON block
        if '```json' in response_text:
            json_str = response_text.split('```json')[1].split('```')[0].strip()
        elif '```' in response_text:
            json_str = response_text.split('```')[1].split('```')[0].strip()
        else:
            json_str = response_text
        
        quizzes = json.loads(json_str)
        
        # Validate structure
        for q in quizzes:
            if not all(k in q for k in ['question', 'options', 'reponse', 'explication', 'niveau']):
                continue
            # Ensure reponse is single letter
            if isinstance(q['reponse'], str) and len(q['reponse']) > 1:
                q['reponse'] = q['reponse'][0].upper()
        
        return quizzes
    
    except Exception as e:
        print(f"Groq error generating quizzes: {e}")
        return []


def evaluate_chapter_quality(chapter_data: dict, chapter_title: str, matiere: str) -> dict:
    """
    Evaluate chapter quality and quiz coverage.
    Returns: {"score": 0-100, "feedback": "...", "suggestions": [...]}
    """
    
    # Check existing quizzes in chapter
    existing_quizzes = extract_existing_quizzes(chapter_data)
    quiz_status = f"Quiz status: {len(existing_quizzes)} valid quizzes found."
    
    if len(existing_quizzes) == 0:
        quiz_status += " MISSING: Chapter needs quiz questions for student testing."
    elif len(existing_quizzes) < 3:
        quiz_status += " INSUFFICIENT: Add more quiz questions (target: 5-6)."
    else:
        quiz_status += " Good quiz coverage."
    
    # Build evaluation context
    chapter_str = json.dumps(chapter_data, ensure_ascii=False)[:400]
    
    prompt = f"""Tu es un expert pédagogue pour le BAC Haïtien.

Matière: {matiere}
Chapitre: {chapter_title}
{quiz_status}

Contenu du chapitre: {chapter_str}

Évalue ce chapitre sur:
1. Clarté pédagogique (0-100)
2. Complétude du contenu (0-100)
3. Pertinence pour le BAC (0-100)
4. Niveau d'engagement (0-100)
5. Couverture des quiz: {len(existing_quizzes)} quiz trouvés

Réponds en JSON:
{{
  "score": 75,
  "feedback": "Résumé général incluant remarques sur les quiz",
  "suggestions": ["Suggestion 1", "Suggestion 2", "Suggestion 3"]
}}"""

    try:
        if not client:
            return {
                "score": 0, 
                "feedback": f"No quizzes found. {quiz_status}", 
                "suggestions": ["Add interactive quiz questions to the chapter", "Ensure quiz format is JSON with question/options/reponse"]
            }
        
        message = client.messages.create(
            model="mixtral-8x7b-32768",
            max_tokens=1000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].text.strip()
        
        # Extract JSON
        if '```json' in response_text:
            json_str = response_text.split('```json')[1].split('```')[0].strip()
        elif '{' in response_text:
            json_str = response_text[response_text.find('{'):response_text.rfind('}')+1]
        else:
            return {
                "score": 0, 
                "feedback": f"Error evaluating. {quiz_status}", 
                "suggestions": ["Add quiz questions", "Verify chapter structure"]
            }
        
        evaluation = json.loads(json_str)
        # Add quiz info to feedback
        evaluation["feedback"] = f"{evaluation.get('feedback', '')} | {quiz_status}"
        return evaluation
    
    except Exception as e:
        return {
            "score": 0, 
            "feedback": f"Evaluation error: {str(e)} | {quiz_status}", 
            "suggestions": ["Fix chapter data structure", "Add quiz questions"]
        }


def improve_chapter_content(chapter_data: dict, chapter_title: str, matiere: str) -> str:
    """
    Get AI suggestions to improve chapter content.
    FIRST checks for existing quizzes and prioritizes quiz-related improvements.
    """
    
    # Check existing quizzes
    existing_quizzes = extract_existing_quizzes(chapter_data)
    quiz_context = ""
    
    if len(existing_quizzes) == 0:
        quiz_context = """
PRIORITE: Ce chapitre n'a PAS de quiz.
- Les quizzes interactifs sont essentiels pour le test de connaissances.
- Suggère d'ajouter des questions évaluatives (5-6 questions minimum)."""
    elif len(existing_quizzes) < 3:
        quiz_context = f"""
NOTE: Ce chapitre a seulement {len(existing_quizzes)} quiz.
- Suggère d'ajouter plus de questions pour une meilleure couverture.
- Cible: 5-6 questions par chapitre."""
    else:
        quiz_context = f"""
STRENGTHS: Ce chapitre a {len(existing_quizzes)} quiz valides.
- Quiz coverage is good.
- Suggère des améliorations pédagogiques du contenu autour des quiz."""
    
    prompt = f"""Tu es un expert pédagogue pour le cours BAC Haïtien ({matiere}).

Chapitre: {chapter_title}
{quiz_context}

Contenu actuel (résumé): {json.dumps(chapter_data, ensure_ascii=False)[:300]}

Donne 3-4 suggestions d'amélioration PRIORITAIRES:
1. D'abord: Suggestions sur les QUIZ (ajouter/améliorer)
2. Ensuite: Améliorations pédagogiques du contenu
3. Format: Recommandations pratiques et spécifiques

Sois concis et actionnable (max 5 lignes par suggestion)."""

    try:
        if not client:
            return f"No client configured. Quiz coverage: {len(existing_quizzes)} quizzes found."
        
        message = client.messages.create(
            model="mixtral-8x7b-32768",
            max_tokens=800,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        return message.content[0].text.strip()
    
    except Exception as e:
        return f"Error generating suggestions: {str(e)}. Current quiz count: {len(existing_quizzes)}"
