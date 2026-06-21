# Groq-powered content improvement endpoints
import json
import logging
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from core.groq_content_improver import generate_quizzes_for_chapter, evaluate_chapter_quality, improve_chapter_content

_logger = logging.getLogger(__name__)


@login_required
@require_POST
def api_generate_quizzes(request):
    """
    POST: {"chapter": {...chapter_data...}, "title": "...", "matiere": "..."}
    Returns: [quiz1, quiz2, ...]
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Requête invalide.'}, status=400)

    chapter = data.get('chapter', {})
    title = data.get('title', 'Unknown')
    matiere = data.get('matiere', 'General')

    try:
        quizzes = generate_quizzes_for_chapter(chapter, title, matiere)
        return JsonResponse({
            'success': True,
            'quizzes': quizzes,
            'count': len(quizzes)
        })
    except Exception as e:
        _logger.exception("api_generate_quizzes error")
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_evaluate_chapter(request):
    """
    POST: {"chapter": {...}, "title": "...", "matiere": "..."}
    Returns: {"score": 0-100, "feedback": "...", "suggestions": [...]}
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Requête invalide.'}, status=400)

    chapter = data.get('chapter', {})
    title = data.get('title', 'Unknown')
    matiere = data.get('matiere', 'General')

    try:
        evaluation = evaluate_chapter_quality(chapter, title, matiere)
        return JsonResponse({
            'success': True,
            'evaluation': evaluation
        })
    except Exception as e:
        _logger.exception("api_evaluate_chapter error")
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_improve_chapter(request):
    """
    POST: {"chapter": {...}, "title": "...", "matiere": "..."}
    Returns: {"suggestions": "..."}
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Requête invalide.'}, status=400)

    chapter = data.get('chapter', {})
    title = data.get('title', 'Unknown')
    matiere = data.get('matiere', 'General')

    try:
        suggestions = improve_chapter_content(chapter, title, matiere)
        return JsonResponse({
            'success': True,
            'suggestions': suggestions
        })
    except Exception as e:
        _logger.exception("api_improve_chapter error")
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)
