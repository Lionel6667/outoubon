import json
import os
import random
import re
import hashlib
import logging
import unicodedata
from datetime import date
from pathlib import Path

from django.conf import settings

from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts.models import DiagnosticResult, UserProfile
from .models import (
    QuizQuestion, QuizSession, ChatMessage, UserStats,
    Flashcard, FlashcardProgress, RevisionPlan, QuizAnalysis, BookmarkedQuestion,
    SubjectChapter, CourseSession, CourseProgressState, GeneratedCourseAsset,
    MistakeTracker, SubjectMastery, ChatSessionSummary, LearningEvent,
    ExtraBetPost, ExtraBetAttempt,
)
from . import gemini
from . import pdf_loader
from .series_data import get_priority_subjects, get_serie_context_text, SERIES
from .exercise_generator import generate_physics_exercise

_logger = logging.getLogger(__name__)


def _parse_json_body(request):
    """Safely parse JSON request body. Returns (data_dict, error_response).
    If parsing fails, returns (None, JsonResponse_400).
    """
    try:
        return json.loads(request.body), None
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({'error': 'Requête invalide.'}, status=400)


SC_SOCIAL_COURSE_KEY = 'sc-social'
PHYSIQUE_COURSE_KEY = 'physique-premium'

PHYSIQUE_BRIEF_EXCLUDED_TITLES = (
    'structure générale',
    'navigation',
    'examens complets',
    'interface utilisateur',
)


def _hybrid_course_key(subject: str, num: int) -> str:
    return f'hybrid-course:{subject}:{num}'


# ── AI context optimization ───────────────────────────────────────────────────
# Subjects where JSON note context is NOT sent to AI (no calculations/formulas needed)
_NO_JSON_CONTEXT_SUBJECTS = frozenset(['anglais', 'espagnol', 'informatique'])

# Map subject → _ai.json file name
_AI_JSON_FILE_MAP = {
    'maths':       'note_math_ai.json',
    'physique':    'note_physique_ai.json',
    'chimie':      'note_de_Chimie_ai.json',
    'svt':         'note_SVT_ai.json',
    'economie':    'note_economie_ai.json',
    'philosophie': 'note_philosophie_ai.json',
    'francais':    'note_kreyol_ai.json',
    'art':         'note_art_ai.json',
    'histoire':    'note_sc_social_ai.json',
}

_AI_BLOCKS_CACHE: dict = {}


def _norm_local_search_text(s: str) -> str:
    s = (s or '').strip().lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _escape_markdown_text(s: str) -> str:
    """Escape markdown chars that break raw scientific notations (e.g. Phi_initial)."""
    if not s:
        return ''
    s = s.replace('\\', '\\\\')
    s = s.replace('_', '\\_')
    return s


def _pick_best_local_exercise_block(subject: str, query: str) -> dict | None:
    """Return the most relevant exercise/example block for exercise-like queries."""
    file_name = _AI_JSON_FILE_MAP.get(subject)
    if not file_name:
        return None

    db_dir = Path(settings.BASE_DIR) / 'database'
    ai_path = db_dir / file_name
    if not ai_path.exists():
        return None

    cache_key = str(ai_path)
    if cache_key not in _AI_BLOCKS_CACHE:
        try:
            data = json.loads(ai_path.read_text(encoding='utf-8-sig', errors='replace'))
            _AI_BLOCKS_CACHE[cache_key] = data.get('blocks', [])
        except Exception:
            _AI_BLOCKS_CACHE[cache_key] = []

    blocks = _AI_BLOCKS_CACHE.get(cache_key, [])
    if not blocks:
        return None

    qnorm = _norm_local_search_text(query)
    if not qnorm:
        return None

    ask_exo_words = ('exo', 'exercice', 'exemple', 'probleme', 'question', 'application')
    if not any(w in qnorm for w in ask_exo_words):
        return None

    stop = {
        'donne', 'moi', 'svp', 'stp', 'peux', 'tu', 'qui', 'quoi', 'comment',
        'sortir', 'sortent', 'habitude', 'exam', 'examen', 'exams', 'bac',
        'haiti', 'haitien', 'dans', 'avec', 'pour', 'les', 'des', 'une', 'the',
    }
    qtokens = [t for t in re.findall(r'\b[a-z0-9]{3,}\b', qnorm) if t not in stop and t not in ask_exo_words]

    # Expand a few physics concept aliases for stronger matching.
    if subject == 'physique':
        if 'magnetisme' in qnorm or 'magnetique' in qnorm:
            qtokens.extend(['magnetique', 'induction', 'laplace', 'bobine', 'faraday', 'lenz'])

    qtoken_set = set(qtokens)
    candidate_types = {'exercise', 'examples', 'detailed_examples'}

    best = None
    best_score = -1

    for b in blocks:
        btype = (b.get('type') or '').strip().lower()
        if btype not in candidate_types:
            continue

        search_zone = _norm_local_search_text(
            (b.get('chapter', '') or '') + ' ' +
            (b.get('subchapter', '') or '') + ' ' +
            (b.get('content', '') or '') + ' ' +
            ' '.join(b.get('tags', []) or [])
        )
        if not search_zone:
            continue

        if qtoken_set:
            exact = sum(1 for t in qtoken_set if t in search_zone)
            if exact == 0:
                continue
        else:
            exact = 0

        type_bonus = {'exercise': 120, 'detailed_examples': 95, 'examples': 85}.get(btype, 0)
        bac_bonus = 20 if ('bac ' in search_zone or '(bac' in search_zone) else 0
        content_len = len((b.get('content', '') or '').strip())
        score = (exact * 100) + type_bonus + bac_bonus + min(content_len, 1800) / 30

        if score > best_score:
            best_score = score
            best = b

    return best


def _search_ai_blocks(subject: str, chapter_num: int, query: str, max_blocks: int = 12) -> str:
    """
    Load note_*_ai.json for subject, filter blocks by chapter_num,
    score by semantic relevance (keywords + type priority), return formatted context.
    Returns empty string if file not found or subject has no AI file.
    Supports French↔Kreyol synonym expansion for 'francais' subject.
    """
    if subject in _NO_JSON_CONTEXT_SUBJECTS:
        return ''

    file_name = _AI_JSON_FILE_MAP.get(subject)
    if not file_name:
        return ''

    db_dir = Path(settings.BASE_DIR) / 'database'
    ai_path = db_dir / file_name
    if not ai_path.exists():
        return ''

    # Cache the parsed file
    cache_key = str(ai_path)
    if cache_key not in _AI_BLOCKS_CACHE:
        try:
            data = json.loads(ai_path.read_text(encoding='utf-8-sig', errors='replace'))
            _AI_BLOCKS_CACHE[cache_key] = data.get('blocks', [])
        except Exception:
            _AI_BLOCKS_CACHE[cache_key] = []

    all_blocks = _AI_BLOCKS_CACHE.get(cache_key, [])
    if not all_blocks:
        return ''

    # Filter to the requested chapter (if chapter_num > 0)
    if chapter_num > 0:
        chapter_blocks = [b for b in all_blocks if b.get('chapter_num') == chapter_num]
    else:
        chapter_blocks = all_blocks[:]

    if not chapter_blocks:
        chapter_blocks = all_blocks

    # Semantic scoring: prioritize true concept match over generic words.
    query_lower = (query or '').lower()

    def _norm_text(s: str) -> str:
        s = (s or '').strip().lower()
        s = unicodedata.normalize('NFD', s)
        s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
        s = re.sub(r'[^a-z0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s
    
    # For 'francais' (Kreyòl), expand French query terms to Kreyòl synonyms
    if subject == 'francais':
        _kr_synonyms = {
            'dissertation': 'pwodiksyon agimantatif tèks ekri',
            'commentaire':  'konpreyansyon tèks',
            'rédaction':    'pwodiksyon ekri',
            'résumé':       'rezime',
            'analyse':      'analiz tèks',
            'texte':        'tèks',
            'étude':        'konpreyansyon',
            'étude de texte': 'konpreyansyon tèks',
            'grammaire':    'gramè',
            'figure de style': 'estil',
            'narrat':       'naratif resi',
        }
        _qry_lower = query_lower
        _extras = [_kr for _fr, _kr in _kr_synonyms.items() if _fr in _qry_lower]
        if _extras:
            query_lower = query_lower + ' ' + ' '.join(_extras)

    # Subject-specific query expansion to improve matching for common BAC phrasing.
    _subject_synonyms = {
        'physique': {
            'magnetisme': 'magnetique induction laplace bobine champ electromagnetique galvanometre faraday',
            'aimant': 'champ magnetique induction',
            'courant alternatif': 'sinusoidal rlc resonance reactance impedance',
        },
        'chimie': {
            'acide base': 'ph neutralisation titrage',
        },
    }
    _syn_map = _subject_synonyms.get(subject, {})
    _qnorm_probe = _norm_text(query_lower)
    _extra_terms = [v for k, v in _syn_map.items() if k in _qnorm_probe]
    if _extra_terms:
        query_lower = query_lower + ' ' + ' '.join(_extra_terms)

    _stop_tokens = {
        'donne', 'moi', 'svp', 'stp', 'peux', 'tu', 'question', 'reponse',
        'exo', 'exercice', 'exemple', 'habitude', 'sortir', 'sortent',
        'exam', 'exams', 'examen', 'bac', 'haiti', 'haitien', 'locale',
        'recherche', 'dans', 'sur', 'avec', 'pour', 'les', 'des', 'une', 'dans',
        'that', 'this', 'from', 'about', 'help', 'please',
    }

    query_norm = _norm_text(query_lower)
    query_tokens = {
        tok for tok in re.findall(r'\b[a-z0-9]{3,}\b', query_norm)
        if tok not in _stop_tokens
    }

    TYPE_PRIORITY = {
        'chapter_summary': 0, 'definition': 1, 'explanation': 2, 'method': 3,
        'examples': 4, 'detailed_examples': 5, 'methods': 6,
        'common_mistakes': 7, 'summary': 8, 'exercise': 9,
    }

    def _score_block(block):
        """
        Composite score: keyword match (strong) + type priority (medium) + content length (weak).
        """
        raw_text = (
            block.get('content', '') + '\n' +
            block.get('chapter', '') + '\n' +
            block.get('subchapter', '') + '\n' +
            ' '.join(block.get('tags', []))
        )
        text_to_search = _norm_text(raw_text)

        block_tokens = set(re.findall(r'\b[a-z0-9]{3,}\b', text_to_search))

        # Keyword match: exact token overlap + weak prefix overlap for morphological variants.
        exact_matches = sum(1 for tok in query_tokens if tok in block_tokens)
        prefix_matches = 0
        if query_tokens:
            for tok in query_tokens:
                if len(tok) >= 5:
                    root = tok[:5]
                    if any(bt.startswith(root) for bt in block_tokens):
                        prefix_matches += 1

        keyword_score = (exact_matches * 3) + prefix_matches

        # Bonus if the concept appears in title/subchapter fields.
        title_zone = _norm_text((block.get('chapter', '') or '') + ' ' + (block.get('subchapter', '') or ''))
        title_bonus = sum(2 for tok in query_tokens if tok in title_zone)
        
        # Type priority (lower = better)
        type_priority = TYPE_PRIORITY.get(block.get('type', ''), 99)
        
        # Content length as tiebreaker (prefer non-empty blocks)
        content_len = len(block.get('content', '').strip())
        
        # Composite: very strong weight on semantic match, medium on pedagogic type.
        return (keyword_score * 1200) + (title_bonus * 700) - (type_priority * 12) + (min(content_len, 500) / 500)

    # Score and sort
    scored_blocks = [(score := _score_block(b), b) for b in chapter_blocks]
    scored_blocks.sort(key=lambda x: x[0], reverse=True)

    top_score = scored_blocks[0][0] if scored_blocks else 0

    # If user asked a specific query but nothing semantically matched, return empty
    # so caller can handle with a precise "not found" message instead of random content.
    if query_tokens and top_score <= 0:
        return ''

    # Select top blocks, preferring those with score > 0 (matched query)
    selected = []
    for score, block in scored_blocks:
        if len(selected) >= max_blocks:
            break
        selected.append(block)

    # Only use type fallback for broad/no-keyword prompts.
    if not selected or (not query_tokens and scored_blocks and scored_blocks[0][0] <= 0):
        selected = sorted(chapter_blocks, key=lambda b: TYPE_PRIORITY.get(b.get('type', ''), 99))[:max_blocks]

    # Format as context text
    if not selected:
        return ''

    lines = []
    prev_chapter = None
    prev_sub = None

    for block in selected:
        chapter = block.get('chapter', '')
        sub = block.get('subchapter', '')
        btype = block.get('type', '')
        content = block.get('content', '').strip()

        if not content:
            continue

        if chapter != prev_chapter:
            lines.append(f"\n## {chapter}")
            prev_chapter = chapter
            prev_sub = None

        if sub != prev_sub:
            lines.append(f"\n### {sub}")
            prev_sub = sub

        lines.append(f"\n**[{btype}]**\n{content}\n")

    return '\n'.join(lines) if lines else ''


def _get_user_lang(request) -> str:
    """
    Retourne la langue de l'élève: 'fr' ou 'kr'.
    Priorité : header X-User-Lang → UserProfile.preferred_lang → 'fr'.
    """
    lang = request.headers.get('X-User-Lang', '').strip()
    if lang in ('fr', 'kr'):
        return lang
    if request.user.is_authenticated:
        try:
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            return profile.preferred_lang or 'fr'
        except Exception:
            pass
    return 'fr'


def _load_physique_brief_text() -> str:
    brief_path = Path(__file__).resolve().parent.parent / 'database' / 'note_physique.json'
    try:
        return brief_path.read_text(encoding='utf-8')
    except OSError:
        return ''


def _extract_physique_summary(content: str) -> str:
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(('|', '\\[', '\\]', '```')):
            continue
        if re.match(r'^\*\*Test rapide', line, flags=re.IGNORECASE):
            continue
        line = re.sub(r'^[-*]\s+', '', line)
        line = re.sub(r'^\d+[.)]\s+', '', line)
        line = re.sub(r'[`*_>#]+', '', line).strip()
        if len(line) < 24:
            continue
        return line[:190]
    return ''


def _parse_physique_brief(raw_text: str) -> dict:
    lines = raw_text.splitlines()
    sections = []
    intro_lines = []
    current = None

    for line in lines:
        heading_match = re.match(r'^(#{2,4})\s+(.*)$', line)
        if heading_match:
            if current:
                current['content'] = '\n'.join(current['content']).strip()
                sections.append(current)
            current = {
                'id': f'phys-section-{len(sections) + 1}',
                'level': len(heading_match.group(1)),
                'title': heading_match.group(2).strip(),
                'content': [],
            }
            continue

        if current:
            current['content'].append(line)
        else:
            intro_lines.append(line)

    if current:
        current['content'] = '\n'.join(current['content']).strip()
        sections.append(current)

    stack = []
    for index, section in enumerate(sections):
        while stack and stack[-1]['level'] >= section['level']:
            stack.pop()
        section['parents'] = [item['title'] for item in stack]
        next_level = sections[index + 1]['level'] if index + 1 < len(sections) else 0
        section['leaf'] = next_level <= section['level']
        section['summary'] = _extract_physique_summary(section['content'])
        stack.append(section)

    student_outline = []
    section_map = {}
    for section in sections:
        lowered = section['title'].lower()
        if not section['leaf']:
            continue
        if any(token in lowered for token in PHYSIQUE_BRIEF_EXCLUDED_TITLES):
            continue
        if not section['content'].strip():
            continue

        category = section['parents'][-1] if section['parents'] else 'Parcours Physique'
        family = 'pratique' if section['title'].startswith('3.') else 'cours'
        item = {
            'id': section['id'],
            'title': section['title'],
            'level': section['level'],
            'category': category,
            'family': family,
            'summary': section['summary'] or 'Lecon generee par l IA a partir du brief pedagogique interne.',
            'parents': section['parents'],
        }
        student_outline.append(item)
        section_map[section['id']] = {
            'id': section['id'],
            'title': section['title'],
            'level': section['level'],
            'category': category,
            'family': family,
            'summary': item['summary'],
            'parents': section['parents'],
            'content': section['content'],
        }

    return {
        'intro': '\n'.join(intro_lines).strip(),
        'outline': student_outline,
        'section_map': section_map,
    }


def _get_physique_course_data() -> dict:
    return _parse_physique_brief(_load_physique_brief_text())


def _clean_weak_points(payload, limit=8):
    if not isinstance(payload, list):
        return []
    cleaned = []
    for value in payload[:limit]:
        if isinstance(value, str):
            text = value.strip()
            if text:
                cleaned.append(text[:180])
    return cleaned


def _build_physique_section_context(section: dict) -> tuple[str, str, str]:
    chapter_title = section['category'] or 'Physique Bac'
    parent_hint = ' > '.join(section.get('parents') or [])
    chapter_context = (
        'Brief interne de preparation du cours de Physique pour le Bac Haitien. '
        'Ce brief ne doit pas etre affiche tel quel a l eleve. '
        f'Parcours: {parent_hint or "Physique"}.\n\n'
        f'Objectif de la partie: {section["title"]}.\n\n'
        f'Consignes internes et points a couvrir:\n{section["content"][:4000]}'
    )
    exam_related = pdf_loader.get_exam_text_for_section('physique', section['title'], chapter_title)
    return chapter_title, chapter_context, exam_related


def _get_cached_generated_asset(section_id: str, asset_type: str, mode: str = 'normal'):
    return GeneratedCourseAsset.objects.filter(
        course_key=PHYSIQUE_COURSE_KEY,
        section_id=section_id,
        asset_type=asset_type,
        mode=mode,
    ).first()


def _get_physique_shared_assets_payload() -> dict:
    shared_assets = {}
    assets = GeneratedCourseAsset.objects.filter(
        course_key=PHYSIQUE_COURSE_KEY,
        mode='normal',
    )
    for asset in assets:
        bucket = shared_assets.setdefault(asset.section_id, {})
        if asset.asset_type == 'lesson' and isinstance(asset.payload, dict):
            bucket['content'] = str(asset.payload.get('content', '') or '')
        elif asset.asset_type == 'quiz' and isinstance(asset.payload, dict):
            questions = asset.payload.get('questions', [])
            if isinstance(questions, list):
                bucket['quiz'] = questions
        elif asset.asset_type == 'exercise_bank' and isinstance(asset.payload, dict):
            exercises = asset.payload.get('exercises', [])
            if isinstance(exercises, list):
                bucket['exercise_bank'] = exercises
    return shared_assets


def _store_generated_asset(section: dict, asset_type: str, payload: dict, mode: str = 'normal'):
    asset, _ = GeneratedCourseAsset.objects.update_or_create(
        course_key=PHYSIQUE_COURSE_KEY,
        section_id=section['id'],
        asset_type=asset_type,
        mode=mode,
        defaults={
            'section_title': section['title'],
            'payload': payload,
        },
    )
    return asset


def _load_physique_course_json():
    """Charge le contenu 100% original du cours de physique depuis chapters_physique.json"""
    try:
        # Essayer d'abord chapters_physique.json (plus structuré)
        json_path = Path(__file__).parent.parent / 'database' / 'json' / 'chapters_physique.json'
        if json_path.exists():
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # Fallback sur note_physique.json
        json_path = Path(__file__).parent.parent / 'database' / 'note_physique.json'
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Impossible de charger physique JSON: {e}")
        return {}


def _get_physique_lesson_from_json(section_id: str) -> str:
    """Extrait le contenu original du JSON pour une section donnée (100% original, pas d'IA)"""
    course_data = _load_physique_course_json()
    chapters = course_data.get('chapters', [])
    
    for chapter in chapters:
        # Match par titre du chapitre ou ID
        chapter_title = chapter.get('title', '').lower().strip()
        chapter_id = chapter.get('id', '').lower().strip()
        section_id_lower = section_id.lower().strip()
        
        if section_id_lower in chapter_title or section_id_lower == chapter_id or chapter_title in section_id_lower:
            # Formater le contenu du chapitre en HTML/Markdown
            content = f"# {chapter.get('title', '')}\n\n"
            
            # Ajouter le résumé si présent
            if chapter.get('summary'):
                content += f"**Résumé:** {chapter.get('summary')}\n\n"
            
            # Ajouter le contenu brut du chapitre (de chapters_physique.json)
            if chapter.get('text'):
                content += chapter.get('text') + "\n\n"
            
            # Ajouter les contenus structurés si présents
            if chapter.get('contenus'):
                content += "## Contenus principaux\n\n"
                for contenu in chapter.get('contenus', []):
                    content += f"- {contenu}\n"
                content += "\n"
            
            # Ajouter les compétences si présentes
            if chapter.get('competences'):
                content += "## Compétences à acquérir\n\n"
                for competence in chapter.get('competences', []):
                    content += f"- {competence}\n"
                content += "\n"
            
            return content.strip() if content.strip() else None
    
    return None



def _get_or_generate_physique_lesson(section: dict, mode: str = 'normal', weak_points=None) -> str:
    """
    Retourne la leçon pour une section physique.
    **VERSION 100% ORIGINAL-ONLY**: Aucune génération IA. 
    Utilise UNIQUEMENT chapters_physique.json
    """
    section_id = section.get('id', '')
    
    # **BLOQUER LE CACHE** - Ne jamais utiliser les assets générés par l'IA
    # (Tous les vieux caches IA ont été supprimés)
    
    # **UNIQUEMENT**: Charger depuis le JSON original
    original_content = _get_physique_lesson_from_json(section_id)
    if original_content:
        return original_content
    
    # **PAS DE FALLBACK IA** - Si pas dans JSON, retourner une erreur claire
    error_msg = f"Section '{section_id}' non disponible. Seul le contenu du programme officiel est utilisé."
    print(f"[ERROR] {error_msg}")
    return f"⚠️ {error_msg}"


def _get_or_generate_physique_quiz(section: dict, mode: str = 'normal', weak_points=None) -> list:
    """
    Retourne les questions de quiz pour une section physique.
    **VERSION 100% ORIGINAL-ONLY**: Aucune génération IA.
    Pour maintenant: Retourner un message ou liste vide (pas d'IA)
    """
    # **PAS DE GÉNÉRATION IA** - Pas de quiz jusqu'à ce qu'il y en ait dans chapters_physique.json
    return []


def _extract_physique_problem_references(content: str) -> tuple[list[str], list[str]]:
    exercise_types = []
    references = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        type_match = re.match(r'^-\s*\*\*Exercices types\*\*\s*:\s*(.*)$', line, flags=re.IGNORECASE)
        if type_match:
            exercise_types = [item.strip(' .') for item in type_match.group(1).split(',') if item.strip()]
            continue
        ref_match = re.match(r'^-\s*`?([^`]+?\.pdf)`?\s*[–-]\s*(.*)$', line)
        if ref_match:
            references.append(f"{ref_match.group(1).strip()} — {ref_match.group(2).strip()}")
    return exercise_types, references


def _build_physique_parameterized_exercise(section: dict, source_ref: str, index: int, exercise_types: list[str]) -> dict:
    seed = int(hashlib.md5(f"{section['id']}::{source_ref}::{index}".encode('utf-8')).hexdigest()[:8], 16)
    title = section['title']
    theme = title

    def choice(values):
        return values[seed % len(values)]

    questions = []
    intro = ''
    solution = ''
    conseils = ''

    lowered = title.lower()
    if 'champ magn' in lowered:
        variant = index % 3
        if variant == 0:
            current = [8, 10, 12, 15][seed % 4]
            distance = [0.02, 0.03, 0.04, 0.05][(seed // 5) % 4]
            intro = (
                f"Un fil rectiligne tres long est parcouru par un courant continu de ${current}\\,A$. "
                f"On etudie le champ magnetique en un point situe a ${distance}\\,m$ du fil, dans l esprit de {source_ref}."
            )
            questions = [
                "Calcule l intensite du champ magnetique $B$ au point considere.",
                "Precise la direction et le sens du vecteur champ magnetique en utilisant la regle de la main droite.",
                "Determine la nouvelle valeur de $B$ si on double l intensite du courant, puis si on double la distance au fil.",
            ]
            solution = (
                "Pour un fil rectiligne infini, on utilise $B = \\dfrac{\\mu_0 I}{2\\pi d}$ ou $\\mu_0 = 4\\pi \\times 10^{-7}$ T·m/A. "
                "Le champ est tangent aux lignes circulaires centrees sur le fil et son sens se determine avec la regle de la main droite. "
                "Si $I$ double, $B$ double; si $d$ double, $B$ est divise par 2."
            )
            conseils = "Commence par convertir proprement la distance en metre puis rappelle la formule avant le calcul numerique."
            hints = [
                "Applique la formule $B = \\dfrac{\\mu_0 I}{2\\pi d}$ ou $\\mu_0 = 4\\pi \\times 10^{-7}$ T·m/A. Remplace $I = " + str(current) + "\\,A$ et $d = " + str(distance) + "\\,m$.",
                "La regle de la main droite: pouce dans le sens du courant, les doigts s enroulent autour du fil dans le sens du champ. Verifie que tu tournes bien autour de l axe du fil.",
                "La formule montre: si $I$ double, $B$ double. Si $d$ double, le denominateur double donc $B$ est divise par 2 (relation inverse)."
            ]
        elif variant == 1:
            n = [400, 500, 600, 800][seed % 4]
            length = [0.4, 0.5, 0.6, 0.8][(seed // 5) % 4]
            current = [1.5, 2.0, 2.5, 3.0][(seed // 11) % 4]
            intro = (
                f"Un long solenoide de {n} spires et de longueur ${length}\\,m$ est traverse par un courant de ${current}\\,A$. "
                f"On veut etudier le champ magnetique regnant pres de son centre. Situation inspiree de {source_ref}."
            )
            questions = [
                "Calcule la valeur du champ magnetique $B$ au centre du solenoide.",
                "Explique pourquoi ce champ peut etre considere comme uniforme au voisinage du centre.",
                "Compare ce champ a celui obtenu si le nombre de spires est double sans changer la longueur ni le courant.",
            ]
            solution = (
                "Au centre d un solenoide long, $B = 4\\pi \\times 10^{-7} \\times \\dfrac{N}{L} \\times I$. "
                "Le champ y est pratiquement uniforme car les lignes de champ sont paralleles et equidistantes dans la zone centrale. "
                "Si $N$ double, $B$ double egalement."
            )
            conseils = "Repere bien la grandeur $N/L$, c est elle qui mesure la densite de spires."
            hints = [
                "Utilise la formule au centre du solenoide: $B = 4\\pi \\times 10^{-7} \\times \\dfrac{N}{L} \\times I$ avec $N = " + str(n) + "$, $L = " + str(length) + "\\,m$, $I = " + str(current) + "\\,A$.",
                "Le champ est uniforme pres du centre car les lignes de champ y sont paralleles et equidistantes. Cette uniformite est approximativement vraie loin des extremites du solenoide.",
                "La densite de spires est $N/L$. Si $N$ double et $L$ reste constant, ce ratio double, donc $B$ double. C est un resultat direct de la formule."
            ]
        else:
            bh = [2.0e-5, 2.2e-5, 2.5e-5, 3.0e-5][seed % 4]
            bv = [3.0e-5, 3.4e-5, 3.8e-5, 4.2e-5][(seed // 5) % 4]
            intro = (
                f"Dans une region donnee, le champ magnetique terrestre possede une composante horizontale ${bh:.2e}\\,T$ "
                f"et une composante verticale ${bv:.2e}\\,T$. Exercice construit dans l esprit de {source_ref}."
            )
            questions = [
                "Calcule l intensite totale du champ magnetique terrestre $B$.",
                "Determine l angle d inclinaison magnetique du champ par rapport a l horizontale.",
                "Explique la difference entre inclinaison magnetique et declinaison magnetique.",
            ]
            solution = (
                "L intensite totale vaut $B = \\sqrt{B_h^2 + B_v^2}$. "
                "L inclinaison $i$ verifie $\\tan i = \\dfrac{B_v}{B_h}$. "
                "L inclinaison compare le champ a l horizontale, tandis que la declinaison compare le meridien magnetique au meridien geographique."
            )
            conseils = "Ne confonds pas somme vectorielle et somme simple: il faut utiliser Pythagore pour $B$."
            hints = [
                "Tu as deux composantes: $B_h = " + f"{bh:.2e}" + "\\,T$ (horizontale) et $B_v = " + f"{bv:.2e}" + "\\,T$ (verticale). Utilise Pythagore: $B = \\sqrt{B_h^2 + B_v^2}$.",
                "L angle d inclinaison est appele aussi 'dip angle'. Tu calcules son sinus ou tangente selon la definition locale. La tangente est $\\tan i = B_v / B_h$.",
                "L inclinaison mesure l angle par rapport a l horizontale (composantes verticale et horizontale). La declinaison mesure l angle entre le nord magnetique et le nord geographique (rotation dans le plan horizontal)."
            ]
    elif 'induction' in lowered or 'flux' in lowered or 'faraday' in lowered:
        variant = seed % 3
        if variant == 0:
            b = [0.2, 0.25, 0.3, 0.4][seed % 4]
            area = [8e-4, 1e-3, 1.2e-3, 1.5e-3][(seed // 5) % 4]
            angle = [0, 30, 45, 60][(seed // 11) % 4]
            intro = (
                f"Une spire plane de surface ${area:.2e}\\,m^2$ est placee dans un champ uniforme de ${b}\\,T$. "
                f"La normale a la spire fait un angle de ${angle}^\\circ$ avec le champ. Situation inspiree de {source_ref}."
            )
            questions = [
                "Calcule le flux magnetique $\\Phi$ a travers la spire.",
                "Determine le flux si la spire devient parallele aux lignes de champ.",
                "Indique dans quel cas le flux est maximal et justifie."
            ]
            solution = (
                "On applique $\\Phi = BS\\cos \\theta$, ou $\\theta$ est l angle entre le champ et la normale a la surface. "
                "Le flux est nul si la normale est perpendiculaire au champ, et maximal en valeur absolue si la normale lui est parallele."
            )
            conseils = "Fais attention a l angle de la normale, pas a l angle du plan lui-meme."
            hints = [
                "Utilise $\\Phi = BS\\cos \\theta$ ou $B = " + str(b) + "\\,T$, $S = " + f"{area:.2e}" + "\\,m^2$, et $\\theta = " + str(angle) + "^\\circ$ est l angle entre le champ et la NORMALE a la spire.",
                "Si la spire devient parallele aux lignes, la normale devient perpendiculaire au champ, donc $\\theta$ devient $90^\\circ$ et $\\cos(90^\\circ) = 0$.",
                "Le flux est maximal quand $\\cos \\theta = \\pm 1$, ce qui arrive quand la normale est parallele ou antiparallele au champ ($\\theta = 0^\\circ$ ou $180^\\circ$)."
            ]
        elif variant == 1:
            delta_phi = [2e-3, 3e-3, 4e-3, 5e-3][seed % 4]
            delta_t = [0.02, 0.05, 0.08, 0.1][(seed // 5) % 4]
            intro = (
                f"Le flux magnetique traversant un circuit ferme varie de ${delta_phi:.2e}\\,Wb$ en ${delta_t}\\,s$. "
                f"On cherche la f.e.m. induite produite dans ce circuit, comme dans {source_ref}."
            )
            questions = [
                "Calcule la valeur moyenne de la f.e.m. induite.",
                "Explique la signification du signe moins dans la loi de Faraday-Lenz.",
                "Determine la f.e.m. moyenne si la meme variation de flux a lieu deux fois plus vite."
            ]
            solution = (
                "La loi de Faraday donne $e_{moy} = -\\dfrac{\\Delta \\Phi}{\\Delta t}$. "
                "Le signe moins traduit l opposition du courant induit a la cause qui lui donne naissance. "
                "Si la duree est divisee par 2, la valeur absolue de la f.e.m. est multipliee par 2."
            )
            conseils = "Calcule d abord la valeur absolue, puis interprete physiquement le signe."
            hints = [
                "Applique la formule de Faraday: $|e| = \\dfrac{|\\Delta \\Phi|}{\\Delta t} = \\dfrac{" + f"{delta_phi:.2e}" + "}{" + str(delta_t) + "}$. Le signe moins reste dans l interpretation.",
                "Le signe moins de Lenz signifie que le courant induit crée un champ qui s oppose a la variation du flux original. C est une loi de compensation ou de resistance au changement.",
                "Si on reduit le temps de moitie, le denominateur devient deux fois plus petit, donc la f.e.m. double. La variation de flux plus rapide produit une f.e.m. plus grande."
            ]
        else:
            delta_phi = [1.5e-3, 2.0e-3, 2.5e-3, 3.0e-3][seed % 4]
            resistance = [2, 4, 5, 8][(seed // 5) % 4]
            intro = (
                f"Dans un circuit ferme de resistance ${resistance}\\,\\Omega$, le flux magnetique varie de ${delta_phi:.2e}\\,Wb$. "
                f"On veut determiner la quantite d electricite induite. Exercice inspire de {source_ref}."
            )
            questions = [
                "Calcule la quantite d electricite induite $Q$ qui traverse le circuit.",
                "Precise de quelles grandeurs depend $Q$.",
                "Explique pourquoi cette quantite est independante de la duree de la variation du flux."
            ]
            solution = (
                "Lors d une variation finie du flux, on utilise $Q = \\dfrac{|\\Delta \\Phi|}{R}$. "
                "La charge induite depend donc de la variation de flux et de la resistance, mais pas directement de la duree."
            )
            conseils = "Identifie bien si l on demande une charge totale ou une f.e.m.; les deux formules ne sont pas les memes."
            hints = [
                "La charge totale est $Q = \\dfrac{|\\Delta \\Phi|}{R}$ avec $\\Delta \\Phi = " + f"{delta_phi:.2e}" + "\\,Wb$ et $R = " + str(resistance) + "\\,\\Omega$.",
                "$Q$ ne depend que de la variation totale du flux et de la resistance. Plus la resistance est grande, moins la charge passe (moins de courant pour la meme f.e.m.).",
                "La charge ne depend PAS du temps parce que si change plus vite, la f.e.m. est plus grande mais la duree est plus courte: ces deux effets se compensent pour le total de charge."
            ]
    elif 'sol' in lowered or 'bobine' in lowered or 'inductance' in lowered:
        n = [200, 300, 400, 500][seed % 4]
        length = [0.25, 0.3, 0.4, 0.5][(seed // 5) % 4]
        current = [1.2, 1.5, 2.0, 2.4][(seed // 11) % 4]
        section_area = [2.5e-4, 3.0e-4, 4.0e-4, 5.0e-4][(seed // 17) % 4]
        intro = (
            f"Un solenoide de {n} spires, de longueur ${length}\,m$ et de section ${section_area:.2e}\,m^2$, "
            f"est parcouru par un courant continu de ${current}\,A$. On l etudie en s inspirant du sujet {source_ref}."
        )
        questions = [
            "Calcule le champ magnetique $B$ au centre du solenoide.",
            "Determine le flux magnetique propre $\\Phi$ a travers une spire.",
            "En deduis l inductance $L$ de la bobine puis l energie stockee.",
        ]
        solution = (
            "On utilise d abord $B = 4\\pi \\times 10^{-7} \\times \\dfrac{N}{l} \\times I$. "
            "Ensuite le flux propre s obtient par $\\Phi = B S$ pour une spire, puis l inductance par $L = \\dfrac{N\\Phi}{I}$. "
            "Enfin l energie magnetique se calcule avec $E = \\dfrac{1}{2}LI^2$."
        )
        conseils = "Pose soigneusement les unites, puis garde la meme logique: champ, flux, inductance, energie."
        hints = [
            "Champ magnetique: $B = 4\\pi \\times 10^{-7} \\times \\dfrac{N}{l} \\times I$ avec $N = " + str(n) + "$, $l = " + str(length) + "\\,m$, $I = " + str(current) + "\\,A$.",
            "Flux propre a travers UNE spire: $\\Phi = B \\times S$ ou $S = " + f"{section_area:.2e}" + "\\,m^2$. Puis l inductance totale: $L = \\dfrac{N \\times \\Phi}{I}$.",
            "Energie stockee dans l inductance: $E = \\dfrac{1}{2} L I^2$. L inductance mesure la capacite de la bobine a emmagasiner de l energie magnetique."
        ]
    elif 'laplace' in lowered:
        b = [0.08, 0.1, 0.12, 0.15][seed % 4]
        current = [3.0, 4.0, 5.0, 6.0][(seed // 5) % 4]
        length = [0.18, 0.2, 0.24, 0.3][(seed // 11) % 4]
        intro = (
            f"Une tige conductrice de longueur ${length}\,m$ parcourue par un courant de ${current}\,A$ est placee dans un champ uniforme "
            f"de ${b}\,T$, perpendiculairement aux lignes de champ. Situation inspiree de {source_ref}."
        )
        questions = [
            "Determine la valeur de la force de Laplace exercee sur la tige.",
            "Precise le sens de la force en utilisant la regle des trois doigts.",
            "Calcule le travail de cette force si la tige se deplace de $0,12\,m$ dans son sens.",
        ]
        solution = "On applique $F = BIL\\sin \\alpha$ avec $\\alpha = 90^\\circ$, puis $W = Fd$ pour le travail."
        conseils = "Verifie d abord si le conducteur est parallele ou perpendiculaire au champ avant de calculer."
        hints = [
            "Force de Laplace: $F = B I L \\sin \\alpha$ ou $B = " + str(b) + "\\,T$, $I = " + str(current) + "\\,A$, $L = " + str(length) + "\\,m$. Ici $\\alpha = 90^\\circ$ donc $\\sin \\alpha = 1$.",
            "Regle des trois doigts: pouce = courant, index = champ, majeur = force. Ou utilise la regle de la main droite en croisant les doigts.",
            "Travail quand la force et le deplacement sont paralleles: $W = F \\times d = F \\times 0.12\\,m$. C est une energie fournie au systeme."
        ]
    elif 'galvanom' in lowered:
        n = [80, 100, 120, 150][seed % 4]
        area = [2.0e-4, 2.5e-4, 3.0e-4, 4.0e-4][(seed // 5) % 4]
        b = [0.12, 0.15, 0.18, 0.2][(seed // 11) % 4]
        k = [2.0e-5, 2.5e-5, 3.0e-5, 3.5e-5][(seed // 17) % 4]
        intro = (
            f"Un galvanometre a cadre mobile comporte {n} spires de surface ${area:.2e}\,m^2$ dans un champ radial de ${b}\,T$. "
            f"La constante de torsion vaut ${k:.2e}\,N\\cdot m/rad$. Situation inspiree de {source_ref}."
        )
        questions = [
            "Etablis la relation entre la deviation $\\theta$ et le courant $I$.",
            "Calcule la sensibilite du galvanometre.",
            "Determine la deviation pour un courant de $2\,mA$.",
        ]
        solution = "Le couple electromagnetique $NBSI$ s equilibre avec le couple de torsion $k\\theta$, donc $\\theta = \\dfrac{NBS}{k}I$."
        conseils = "Ne confonds pas le couple de rappel avec la force: ici on travaille sur un equilibre de couples."
        hints = [
            "Couple electromagnetique = $N \\times B \\times S \\times I$ ou $N = " + str(n) + "$, $B = " + str(b) + "\\,T$, $S = " + f"{area:.2e}" + "\\,m^2$. Couple de rappel = $k \\theta$ avec $k = " + f"{k:.2e}" + "\\,N \\cdot m$.",
            "A l equilibre: $N B S I = k \\theta$, donc la sensibilite est $\\dfrac{\\theta}{I} = \\dfrac{NBS}{k}$. C est l angle par unite de courant.",
            "Pour $I = 2\\,mA = 2 \\times 10^{-3}\\,A$, utilise la relation $\\theta = \\dfrac{NBS}{k} \\times I = \\dfrac{NBS}{k} \\times 0.002$."
        ]
    elif 'rlc' in lowered or 'alternatif' in lowered or 'resonance' in lowered:
        r = [20, 30, 40, 50][seed % 4]
        l = [0.08, 0.1, 0.12, 0.15][(seed // 5) % 4]
        c = [40e-6, 50e-6, 60e-6, 80e-6][(seed // 11) % 4]
        f = [50, 60, 75, 100][(seed // 17) % 4]
        intro = (
            f"Un circuit RLC serie est alimente sous tension alternative. On donne $R={r}\\,\\Omega$, $L={l}\\,H$, "
            f"$C={c:.2e}\\,F$ et $f={f}\\,Hz$. Exercice inspire de {source_ref}."
        )
        questions = [
            "Calcule les reactances $X_L$ et $X_C$ puis l impedance $Z$ du circuit.",
            "Determine l intensite efficace du courant si la tension efficace vaut $120\,V$.",
            "Precise si le circuit est inductif, capacitif ou en resonance.",
        ]
        solution = "On utilise $X_L = L\\omega$, $X_C = \\dfrac{1}{C\\omega}$, puis $Z = \\sqrt{R^2 + (X_L-X_C)^2}$ et $I = \\dfrac{U}{Z}$."
        conseils = "Commence toujours par $\\omega = 2\\pi f$, puis compare $X_L$ et $X_C$ avant de conclure sur la nature du circuit."
        hints = [
            "D abord: $\\omega = 2\\pi f = 2\\pi \\times " + str(f) + "\\,rad/s$. Puis $X_L = L\\omega = " + str(l) + " \\times \\omega$ et $X_C = \\dfrac{1}{C\\omega} = \\dfrac{1}{" + f"{c:.2e}" + " \\times \\omega}$.",
            "Impedance: $Z = \\sqrt{R^2 + (X_L - X_C)^2}$ avec $R = " + str(r) + "\\,\\Omega$. Puis intensite: $I = \\dfrac{U_{eff}}{Z} = \\dfrac{120}{Z}$.",
            "Si $X_L > X_C$ le circuit est inductif (courant en retard sur tension). Si $X_C > X_L$ il est capacitif (courant en avance). Si $X_L = X_C$ c est la resonance."
        ]
    elif 'chute libre' in lowered:
        h = [45, 60, 80, 100][seed % 4]
        intro = f"Une bille est lachee sans vitesse initiale depuis une hauteur de ${h}\\,m$. Exercice type inspire de {source_ref}."
        questions = [
            "Calcule le temps de chute.",
            "Determine la vitesse juste avant l impact.",
            "Ecris l equation horaire du mouvement vertical en choisissant un repere adapte.",
        ]
        solution = "On utilise $h = \\dfrac{1}{2}gt^2$ puis $v = gt$ si l origine des vitesses est prise a zero."
        conseils = "Annonce ton repere et ton signe pour $g$ avant tout calcul."
        hints = [
            "Hauteur de chute: $h = " + str(h) + "\\,m$. Avec $h = \\dfrac{1}{2}gt^2$ et $g \\approx 10\\,m/s^2$ (ou $9.8\\,m/s^2$), tu encontres: $t = \\sqrt{\\dfrac{2h}{g}}$.",
            "Vitesse juste avant l impact: $v = g \\times t = g \\sqrt{\\dfrac{2h}{g}} = \\sqrt{2gh}$. Cette formule donne directement $v = \\sqrt{2 \\times 10 \\times " + str(h) + "}$ (approximatif).",
            "Equation horaire: Si l axe $z$ pointe vers le bas avec $z=0$ au point de depart, alors $z(t) = \\dfrac{1}{2}gt^2$ et $v_z(t) = gt$. Ton repere doit etre clairement indique."
        ]
    elif 'projectile' in lowered:
        v0 = [20, 25, 30, 35][seed % 4]
        angle = [30, 35, 45, 60][(seed // 5) % 4]
        intro = f"Un projectile est lance avec une vitesse initiale de ${v0}\\,m/s$ sous un angle de ${angle}^\\circ$. Situation inspiree de {source_ref}."
        questions = [
            "Determine les composantes initiales de la vitesse.",
            "Calcule le temps de vol et la portee horizontale.",
            "Determine la hauteur maximale atteinte par le projectile.",
        ]
        solution = "On decompose d abord $V_0$ en $x$ et $y$, puis on traite horizontalement un mouvement uniforme et verticalement un mouvement uniformement varie."
        conseils = "Travaille toujours separement sur les axes $x$ et $y$."
        hints = [
            "Composantes initiales: $v_{0x} = v_0 \\cos(" + str(angle) + "^\\circ) = " + str(v0) + " \\cos(" + str(angle) + "^\\circ)$ et $v_{0y} = v_0 \\sin(" + str(angle) + "^\\circ) = " + str(v0) + " \\sin(" + str(angle) + "^\\circ)$.",
            "Temps de vol (retour a l hauteur initiale): $T = \\dfrac{2 v_{0y}}{g}$. Portee: $x_{max} = v_{0x} \\times T = \\dfrac{v_{0x} \\times 2 v_{0y}}{g} = \\dfrac{v_0^2 \\sin(2\\theta)}{g}$.",
            "Hauteur maximale: $h_{max} = \\dfrac{v_{0y}^2}{2g}$. C est le moment ou $v_y = 0$. Attend la moitie du temps de vol."
        ]
    elif 'condensateur' in lowered:
        c1 = [2, 3, 4, 5][seed % 4]
        c2 = [4, 6, 8, 10][(seed // 5) % 4]
        u = [60, 90, 120, 150][(seed // 11) % 4]
        intro = f"Deux condensateurs de ${c1}\\,\\mu F$ et ${c2}\\,\\mu F$ sont montes dans un circuit sous ${u}\\,V$. Exercice inspire de {source_ref}."
        questions = [
            "Calcule la capacite equivalente selon le montage indique.",
            "Determine la charge stockee et l energie emmagasinee.",
            "Explique comment evoluent tension et charge dans le montage.",
        ]
        solution = "On choisit d abord la relation de serie ou de parallele, puis on applique $Q = CU$ et $E = \\dfrac{1}{2}CU^2$."
        conseils = "Identifie toujours quelle grandeur est commune: tension ou charge."
        hints = [
            "Si les condensateurs sont en PARALLELE: $C_{eq} = C_1 + C_2 = " + str(c1) + " + " + str(c2) + " = " + str(c1 + c2) + "\\,\\mu F$. Si en SERIE: $\\dfrac{1}{C_{eq}} = \\dfrac{1}{" + str(c1) + "} + \\dfrac{1}{" + str(c2) + "}$.",
            "Avec $C = C_{eq}$ et $U = " + str(u) + "\\,V$, calcule: Charge $Q = C_{eq} \\times U$ et Energie $E = \\dfrac{1}{2} C_{eq} \\times U^2$.",
            "En PARALLELE: tension identique aux deux bornes, charges differentes. En SERIE: charge identique sur les deux, tensions differentes. C est l inverse!"
        ]
    else:
        difficulty = choice(['facile', 'moyen', 'moyen', 'avance'])
        intro = f"Exercice de Physique sur le theme {theme}, construit a partir de {source_ref}."
        base_types = exercise_types[:3] if exercise_types else ['analyse des donnees', 'calcul principal', 'interpretation physique']
        questions = [f"Traite la partie suivante: {item}." for item in base_types]
        solution = "Repere les donnees, choisis la loi physique adapte, puis enchaine les calculs en gardant les unites coherentes."
        conseils = "Lis bien le type de grandeur demandee avant de lancer un calcul."
        hints = [
            "Commence par identifier clairement quels sont les donnees utiles et quelle loi physique s applique ici.",
            "Etablis un plan: nomme les grandeurs, identifie la formule, puis substitue les valeurs numeriques.",
            "Termine en verifiant l unite de ta reponse et en interpretant physiquement le resultat."
        ]
        return {
            'title': f"Exercice {index + 1} — {theme}",
            'theme': theme,
            'intro': intro,
            'enonce': intro,
            'questions': questions,
            'solution': solution,
            'conseils': conseils,
            'hints': hints,
            'source': source_ref,
            'difficulte': difficulty,
        }

    difficulty = choice(['moyen', 'moyen', 'avance', 'avance'])
    return {
        'title': f"Exercice {index + 1} — {theme}",
        'theme': theme,
        'intro': intro,
        'enonce': intro,
        'questions': questions,
        'solution': solution,
        'conseils': conseils,
        'hints': hints,
        'source': source_ref,
        'difficulte': difficulty,
    }


def _get_progressive_exercise_difficulty(index: int) -> str:
    if index <= 0:
        return 'facile'
    if index == 1:
        return 'moyen'
    if index == 2:
        return 'avance'
    return 'difficile'


def _build_default_physique_hint(exercise: dict, question: str, question_index: int) -> str:
    question_text = str(question or '').strip()
    lower_question = question_text.lower()
    conseils = str(exercise.get('conseils', '') or '').strip()
    solution = str(exercise.get('solution', '') or '').strip()
    
    # Essayer d'extraire une formule de la solution pour le hint
    import re
    formulas = re.findall(r'\$[^$]+\$', solution)
    
    hint_with_formula = ''
    if formulas:
        # Utiliser la première formule comme base du hint
        first_formula = formulas[0]
        if 'calcule' in lower_question or 'determine' in lower_question:
            hint_with_formula = f'Utilise la formule {first_formula}. Repère d abord les données, puis substitue les valeurs.'
        elif 'explique' in lower_question or 'justifie' in lower_question:
            hint_with_formula = f'La formule {first_formula} montre comment les grandeurs sont liées. Applique-la à la situation et interprète le résultat.'
        elif 'compare' in lower_question:
            hint_with_formula = f'Avec {first_formula}, observe comment la formule change quand tu fais varier la grandeur demandée.'
        elif 'precise le sens' in lower_question or 'direction' in lower_question:
            hint_with_formula = f'La formule {first_formula} montre la relation. Pour la direction, utilise la règle physique (main droite, produit vectoriel, etc.).'
        else:
            hint_with_formula = f'Commence avec la formule {first_formula} et applique-la étape par étape.'
        
        if conseils and 'Conseil' not in hint_with_formula:
            hint_with_formula += f' Astuce: {conseils}'
        return hint_with_formula

    # Fallback si pas de formule trouvée
    if 'calcule' in lower_question or 'determine' in lower_question:
        prefix = 'Repère d abord les données utiles, la formule adaptée et l unité attendue avant de remplacer les valeurs.'
    elif 'explique' in lower_question or 'justifie' in lower_question:
        prefix = 'Appuie ta réponse sur la loi physique du chapitre puis relie-la clairement à la situation décrite.'
    elif 'compare' in lower_question:
        prefix = 'Identifie la grandeur qui varie puis indique comment la formule montre l évolution demandée.'
    elif 'precise le sens' in lower_question or 'direction' in lower_question:
        prefix = 'Fais un schéma mental du phénomène puis utilise la règle ou le repère physique approprié.'
    else:
        prefix = 'Commence par reformuler ce qui est demandé, puis traite la question étape par étape sans oublier l unité.'

    if conseils:
        return f"{prefix} Conseil utile: {conseils}"
    return prefix


def _normalize_physique_exercise(exercise: dict, fallback_index: int) -> dict:
    if not isinstance(exercise, dict):
        return {}

    normalized = dict(exercise)
    normalized['difficulte'] = str(normalized.get('difficulte') or _get_progressive_exercise_difficulty(fallback_index)).strip().lower()
    questions = normalized.get('questions', [])
    if not isinstance(questions, list) or not questions:
        questions = ['Traite la question principale de cet exercice.']
    normalized['questions'] = [str(question or '').strip() for question in questions]

    hints = normalized.get('hints', [])
    if not isinstance(hints, list):
        hints = []
    built_hints = []
    for question_index, question in enumerate(normalized['questions']):
        hint = hints[question_index] if question_index < len(hints) else ''
        if not isinstance(hint, str) or not hint.strip():
            hint = _build_default_physique_hint(normalized, question, question_index)
        built_hints.append(hint.strip())
    normalized['hints'] = built_hints
    return normalized


def _normalize_physique_exercise_bank(exercises: list) -> list:
    if not isinstance(exercises, list):
        return []
    normalized = []
    for index, exercise in enumerate(exercises):
        item = _normalize_physique_exercise(exercise, index)
        if item:
            normalized.append(item)
    return normalized


def _append_generated_physique_exercises(section: dict, exercises: list) -> list:
    if not isinstance(exercises, list) or not exercises:
        return _get_or_generate_physique_exercise_bank(section)

    current_bank = _get_or_generate_physique_exercise_bank(section)
    start_index = len(current_bank)
    appended = []
    for index, exercise in enumerate(exercises):
        appended.append(_normalize_physique_exercise(exercise, start_index + index))

    merged = current_bank + appended
    _store_generated_asset(section, 'exercise_bank', {'exercises': merged}, mode='normal')
    return merged


def _build_physique_stock_exercises(section: dict) -> list:
    exercises = [
        generate_physics_exercise(section['title'], section['id'], index)
        for index in range(3)
    ]
    return exercises


def _is_generic_physique_exercise_bank(exercises: list) -> bool:
    if not isinstance(exercises, list) or not exercises:
        return True
    generic_hits = 0
    for exercise in exercises:
        if not isinstance(exercise, dict):
            generic_hits += 1
            continue
        intro = str(exercise.get('intro', '') or '')
        questions = exercise.get('questions', []) or []
        if intro.startswith('Exercice de Physique sur le theme'):
            generic_hits += 1
            continue
        if questions and all(str(question).startswith('Traite la partie suivante:') for question in questions):
            generic_hits += 1
    return generic_hits == len(exercises)


def _has_progressive_physique_difficulty_bank(exercises: list) -> bool:
    if not isinstance(exercises, list) or not exercises:
        return False
    for index, exercise in enumerate(exercises[:4]):
        if not isinstance(exercise, dict):
            return False
        expected = _get_progressive_exercise_difficulty(index)
        current = str(exercise.get('difficulte', '') or '').strip().lower()
        if current != expected:
            return False
    return True


def _matches_expected_physique_bank_shape(section: dict, exercises: list) -> bool:
    if not isinstance(exercises, list) or not exercises:
        return False

    lowered = str(section.get('title', '') or '').lower()
    intros = [str(item.get('intro', '') or '').lower() for item in exercises if isinstance(item, dict)]
    if len(intros) != len(exercises):
        return False

    if 'champ magn' in lowered:
        expected_tokens = ['fil rectiligne', 'solenoide', 'champ magnetique terrestre']
        if len(intros) < len(expected_tokens):
            return False
        for intro, token in zip(intros[:len(expected_tokens)], expected_tokens):
            if token not in intro:
                return False
    return True


def _get_or_generate_physique_exercise_bank(section: dict) -> list:
    """
    Retourne la banque d'exercices pour une section physique.
    **VERSION 100% ORIGINAL-ONLY**: Aucune génération IA.
    Utilise UNIQUEMENT les exercices du BAC réels (chapters_physique.json + BACExercise table)
    """
    # **NE PAS UTILISER LE CACHE** - Les vieux caches IA ont été supprimés
    
    # **UNIQUEMENT**: Exercices stock depuis PDF/JSON
    stock_exercises = _build_physique_stock_exercises(section)
    if stock_exercises:
        return stock_exercises
    
    # **PAS DE FALLBACK IA** - Pas d'exercices jusqu'à ce qu'ils soient dans le JSON
    print(f"[WARNING] Pas d'exercice stock pour '{section.get('title')}'")
    return []


def _generate_more_physique_exercises(section: dict, seed_exercises: list, count: int = 2, allow_fallbacks: bool = True) -> list:
    import logging
    import random
    logger = logging.getLogger(__name__)
    
    logger.info(f"[_generate_more_physique_exercises] Starting generation: section={section.get('title')}, count={count}, allow_fallbacks={allow_fallbacks}")
    
    chapter_title, chapter_context, _ = _build_physique_section_context(section)
    logger.info(f"[_generate_more_physique_exercises] Context: chapter={chapter_title}, seed_exercises={len(seed_exercises)}")
    
    # Générer une graine unique pour chaque appel (force la variation)
    randomness_seed = random.randint(100000, 999999)
    
    generated = gemini.generate_physics_similar_exercises(
        chapter_title=chapter_title,
        section_title=section['title'],
        internal_context=chapter_context,
        example_exercises=seed_exercises,
        count=count,
        randomness_seed=randomness_seed,
    )
    logger.info(f"[_generate_more_physique_exercises] API returned: {len(generated) if isinstance(generated, list) else 'ERROR - not a list'} items")
    
    if isinstance(generated, list) and len(generated) >= count:
        logger.info(f"[_generate_more_physique_exercises] ✓ SUCCESS! Generated {len(generated)} exercises (needed {count})")
        for item in generated[:count]:
            if isinstance(item, dict):
                item['source'] = 'Exercice similaire IA'
        return _normalize_physique_exercise_bank(generated[:count])

    generated = generated if isinstance(generated, list) else []
    logger.warning(f"[_generate_more_physique_exercises] FAILED: only {len(generated)} items (needed {count})")
    
    if not allow_fallbacks:
        logger.info(f"[_generate_more_physique_exercises] No fallback allowed. Returning empty list.")
        return _normalize_physique_exercise_bank([])

    logger.info(f"[_generate_more_physique_exercises] Using fallback strategy")
    fallback_items = []
    start_index = len(seed_exercises) + len(generated)
    exercise_types, references = _extract_physique_problem_references(section.get('content', ''))
    source_pool = references or [item.get('source') for item in seed_exercises if isinstance(item, dict) and item.get('source')]
    if not source_pool:
        source_pool = [f"Variation guidee — {section['title']}"]

    missing = max(0, count - len(generated))
    logger.info(f"[_generate_more_physique_exercises] Missing {missing} exercises. References found: {len(references) if references else 0}")
    
    if missing > 0 and not references:
        # If no references found, generate a simple fallback exercise
        logger.warning(f"[_generate_more_physique_exercises] Creating manual fallback (no references available)")
        simple_exercise = {
            'title': f'Exercice {start_index + 1} — {section["title"]}',
            'theme': section['title'],
            'intro': f'Exercice supplementaire sur {section["title"]}. Applique les concepts vus dans cette section.',
            'enonce': f'Exercice supplementaire sur {section["title"]}. Applique les concepts vus dans cette section.',
            'questions': ['Traite cette question en appliquant les formules et methodes apprises.'],
            'solution': 'Solution a determiner selon les donnees.',
            'conseils': 'Relis le cours et applique les methodes etape par etape.',
            'hints': ['Commence par identifier les donnees de l exercice et la formule appropriee.'],
            'source': f'Exercice similaire IA — {section["title"]}',
            'difficulte': 'moyen',
        }
        fallback_items.append(_normalize_physique_exercise(simple_exercise, start_index))
        missing -= 1
        logger.info(f"[_generate_more_physique_exercises] Added manual fallback. Remaining missing: {missing}")

    for offset in range(missing):
        fallback_items.append(
            generate_physics_exercise(
                section['title'],
                section['id'],
                start_index + offset,
            )
        )
    return _normalize_physique_exercise_bank(generated + fallback_items)


def _get_user_serie_subjects(user) -> set:
    """Retourne l'ensemble des clés de matières pour la série du user."""
    try:
        profile = user.profile
        serie = profile.serie or 'SVT'
    except Exception:
        serie = 'SVT'
    subjs = set(SERIES.get(serie, SERIES['SVT'])['subjects'].keys())
    # Kreyol doit rester visible hors Examen Blanc.
    subjs.add('francais')
    return subjs


MATS = {
    'maths':       {'label': 'Maths',         'icon': 'fa-square-root-variable', 'color': '#3b82f6'},
    'physique':    {'label': 'Physique',       'icon': 'fa-atom',                 'color': '#8b5cf6'},
    'chimie':      {'label': 'Chimie',         'icon': 'fa-flask',                'color': '#10b981'},
    'svt':         {'label': 'SVT',            'icon': 'fa-leaf',                 'color': '#22c55e'},
    'francais':    {'label': 'Kreyòl',         'icon': 'fa-book-open',            'color': '#f59e0b'},
    'philosophie': {'label': 'Philosophie',    'icon': 'fa-brain',                'color': '#ec4899'},
    'anglais':     {'label': 'Anglais',        'icon': 'fa-globe',                'color': '#06b6d4'},
    'histoire':    {'label': 'Sc Social',       'icon': 'fa-landmark',             'color': '#f97316'},
    'economie':    {'label': 'Économie',       'icon': 'fa-chart-bar',            'color': '#6366f1'},
    'informatique':{'label': 'Informatique',   'icon': 'fa-laptop-code',           'color': '#0ea5e9'},
    'art':         {'label': 'Art',            'icon': 'fa-palette',              'color': '#d946ef'},
    'espagnol':    {'label': 'Espagnol',       'icon': 'fa-language',             'color': '#f43f5e'},
}


def _get_or_create_stats(user):
    stats, _ = UserStats.objects.get_or_create(user=user)
    return stats


def _update_streak(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    today = date.today()
    if profile.last_activity == today:
        return 0  # already counted today
    if profile.last_activity and (today - profile.last_activity).days == 1:
        profile.streak += 1
    else:
        profile.streak = 1
    profile.last_activity = today
    profile.save(update_fields=['streak', 'last_activity'])
    return profile.streak  # newly achieved streak value


# ─────────────────────────────────────────────
# GUEST / DEMO MODE INFRASTRUCTURE
# ─────────────────────────────────────────────

def _is_guest(request):
    """Return True if the visitor is in guest/demo mode (no account)."""
    return request.session.get('guest_mode', False)


def _get_cours_chapters(subject: str) -> list[dict]:
    """
    Returns chapter list for cours pages using note_*.json as the single source.
    """
    subject_norm = (subject or '').strip().lower().replace('-', '_')
    aliases = {
        'kreyol': 'francais',
        'sc_social': 'histoire',
    }
    subject_norm = aliases.get(subject_norm, subject_norm)
    return pdf_loader.get_chapters_from_note_json(subject_norm)

def start_guest_view(request):
    """Start the guest demo session and redirect to the demo dashboard."""
    request.session['guest_mode'] = True
    request.session['guest_quiz_done'] = {}   # {subject: count}
    request.session['guest_exo_done'] = 0
    request.session['guest_serie_pending'] = True  # show series selection on first dashboard load
    request.session.modified = True
    return redirect('dashboard')

def stop_guest_view(request):
    """Clear guest mode and send to the landing page."""
    request.session.pop('guest_mode', None)
    request.session.pop('guest_quiz_done', None)
    request.session.pop('guest_exo_done', None)
    request.session.pop('guest_exam_done', None)
    return redirect('landing')

# Realistic demo data shown to guests
_GUEST_DEMO = {
    'username': 'Visiteur',
    'first_name': 'Visiteur',
    'streak': 3,
    'heures_etude': 2,
    'minutes_rest': 15,
    'avg_score': 62,
    'bac_score': 1178,
    'bac_gap_pass': 0,
    'bac_gap_target': 57,
    # BAC milestone progress bar (1178 is between Assez bien 1140 and Bien 1330)
    'bac_next_milestone_pts': 1330,
    'bac_next_milestone_label': 'Bien',
    'bac_prev_milestone_pts': 1140,
    'bac_milestone_pct': 20,          # (1178-1140)/(1330-1140)*100 ≈ 20%
    # Exercices milestone
    'next_exo_milestone': 10,
    'exo_milestone_pct': 60,          # 6/10 done
    'exo_focus_subject': 'svt',
    # User serie context (SVT demo)
    'user_serie_subjects': ['svt', 'chimie', 'physique', 'maths', 'philosophie', 'histoire', 'anglais', 'francais'],
    'my_xp': 340,
    'current_level': 3,
    'league_rank': 7,
    'league_name': 'Débutant',
    'league_color': '#78716c',
    'xp_progress_pct': 68,
    'global_rank': 47,
    'quiz_scores': {
        'maths':       58,
        'physique':    65,
        'chimie':      72,
        'svt':         55,
        'francais':    80,
        'philosophie': 70,
        'histoire':    63,
        'anglais':     66,
        'economie':    48,
    },
    'strengths': [('francais', 80), ('chimie', 72), ('philosophie', 70)],
    'weaknesses': [('economie', 48), ('svt', 55), ('maths', 58)],
    'league_data': [
        {'rank': 1, 'name': 'Marie T.', 'xp': 820, 'is_me': False},
        {'rank': 2, 'name': 'Jean-Paul', 'xp': 710, 'is_me': False},
        {'rank': 3, 'name': 'Claudia M.', 'xp': 655, 'is_me': False},
        {'rank': 4, 'name': 'Hervé R.', 'xp': 590, 'is_me': False},
        {'rank': 5, 'name': 'Sophia V.', 'xp': 510, 'is_me': False},
        {'rank': 6, 'name': 'André L.', 'xp': 430, 'is_me': False},
        {'rank': 7, 'name': 'Visiteur (vous)', 'xp': 340, 'is_me': True},
        {'rank': 8, 'name': 'Patrick D.', 'xp': 290, 'is_me': False},
        {'rank': 9, 'name': 'Fabiola N.', 'xp': 210, 'is_me': False},
        {'rank': 10, 'name': 'Ricot B.', 'xp': 155, 'is_me': False},
    ],
    'recent_sessions': [
        {'subject': 'maths',    'score': 6,  'total': 10, 'display': 'Mathématiques — 6/10'},
        {'subject': 'chimie',   'score': 8,  'total': 10, 'display': 'Chimie — 8/10'},
        {'subject': 'physique', 'score': 7,  'total': 10, 'display': 'Physique — 7/10'},
    ],
    # Coaching cards (shown on dashboard + progression)
    'coaching_cards': [
        {
            'title': 'Économie — Priorité urgente',
            'description': 'Ton score en Économie (48%) est en dessous du seuil BAC. Commence par la comptabilité nationale : PIB, PNB et la fonction de consommation sont des sujets récurrents aux examens.',
            'icon': 'fas fa-chart-line',
            'color': '#f87171',
            'badge': '⚠ Urgent',
            'action_url': '/dashboard/cours/',
            'action_label': 'Réviser Économie',
        },
        {
            'title': 'SVT — Génétique à renforcer',
            'description': 'En SVT (55%), les chapitres sur la division cellulaire et l\'hérédité mendélienne sont tes points faibles. Une révision ciblée de la méiose et des lois de Mendel t\'aidera beaucoup.',
            'icon': 'fas fa-dna',
            'color': '#fb923c',
            'badge': '⬆ À améliorer',
            'action_url': '/dashboard/cours/',
            'action_label': 'Cours SVT',
        },
        {
            'title': 'Mathématiques — Dérivées & Intégrales',
            'description': 'Tes 58% en Maths montrent des lacunes en calcul différentiel. Pratique les dérivées de fonctions composées et les intégrales par parties — ces sujets représentent ~30% du BAC Maths.',
            'icon': 'fas fa-calculator',
            'color': '#a78bfa',
            'badge': '📈 À consolider',
            'action_url': '/dashboard/quiz/?subject=maths',
            'action_label': 'Quiz Maths',
        },
        {
            'title': 'Kreyòl — Continue comme ça !',
            'description': 'Excellent travail en Kreyòl (80%) ! Tu maîtrises bien la konpreyansyon ak pwoduksyon ekri. Pour atteindre l\'excellence, entraîne-toi sur l\'analiz de tèks ak kòmantè literè en kreyòl.',
            'icon': 'fas fa-pen-nib',
            'color': '#34d399',
            'badge': '✓ Fort',
            'action_url': '/dashboard/exercices/',
            'action_label': 'Exercices Kreyòl',
        },
    ],
    'coach_advice': (
        '<strong>🎯 Analyse de ta progression</strong><br><br>'
        'Après analyse de tes résultats, voici mes recommandations prioritaires :<br><br>'
        '⚠️ <strong>Économie (48%)</strong> — C\'est ta matière la plus faible. Je t\'encourage à commencer immédiatement '
        'par le chapitre sur la <em>comptabilité nationale</em>. Les notions de PIB, PNB et les indicateurs macroéconomiques '
        'sont systématiquement testés au BAC.<br><br>'
        '📊 <strong>SVT (55%)</strong> — La génétique mendélienne et la division cellulaire t\'échappent encore. '
        'Revois les croisements dihybrides et l\'arbre généalogique — deux types de questions très fréquents.<br><br>'
        '✅ <strong>Kreyòl (80%)</strong> et <strong>Chimie (72%)</strong> — Très bon niveau ! '
        'Continue à maintenir ces acquis tout en renforçant tes matières faibles.<br><br>'
        '<em>💡 Conseil du coach : un plan de révision de 8 semaines avec 2h/jour te permettrait d\'atteindre un score BAC estimé à 1 400/1 900.</em>'
    ),
    # Demo flashcards per subject (used when no DB cards exist)
    'demo_flashcards': {
        'maths': [
            {'question': 'Quelle est la définition de la dérivée de f en x₀ ?', 'answer': 'f\'(x₀) = lim[h→0] (f(x₀+h) − f(x₀)) / h, quand cette limite existe.', 'hint': 'Taux de variation instantané', 'difficulty': 2},
            {'question': 'Formule du terme général d\'une suite arithmétique ?', 'answer': 'uₙ = u₀ + n·r  où r est la raison et u₀ le premier terme.', 'hint': 'uₙ₊₁ = uₙ + r', 'difficulty': 1},
            {'question': 'Qu\'est-ce qu\'une intégrale définie ∫ₐᵇ f(x)dx ?', 'answer': 'C\'est l\'aire algébrique sous la courbe de f entre a et b. ∫ₐᵇ f(x)dx = F(b) − F(a) où F est une primitive de f.', 'hint': 'Théorème fondamental de l\'analyse', 'difficulty': 3},
            {'question': 'Que représente le discriminant Δ = b²−4ac ?', 'answer': 'Si Δ>0 : deux racines réelles ; Δ=0 : une racine double ; Δ<0 : pas de racine réelle.', 'hint': 'Équation ax²+bx+c=0', 'difficulty': 1},
            {'question': 'Formule de la dérivée d\'un produit u·v ?', 'answer': '(u·v)\' = u\'·v + u·v\'', 'hint': 'Règle de Leibniz', 'difficulty': 2},
            {'question': 'Qu\'est-ce que la limite d\'une suite (uₙ) en +∞ ?', 'answer': 'L est la limite de (uₙ) si pour tout ε>0, il existe N tel que pour tout n≥N, |uₙ−L|<ε.', 'hint': 'Définition formelle de la convergence', 'difficulty': 3},
        ],
        'chimie': [
            {'question': 'Qu\'est-ce qu\'une réaction d\'oxydoréduction ?', 'answer': 'Réaction avec transfert d\'électrons. L\'oxydant gagne des électrons (se réduit), le réducteur en perd (s\'oxyde).', 'hint': 'OIL RIG : Oxidation Is Loss, Reduction Is Gain', 'difficulty': 2},
            {'question': 'Qu\'est-ce que l\'enthalpie de réaction ΔH ?', 'answer': 'C\'est la chaleur échangée à pression constante. ΔH < 0 : réaction exothermique ; ΔH > 0 : réaction endothermique.', 'hint': 'Thermochimie', 'difficulty': 2},
            {'question': 'Différence entre alcane, alcène et alcyne ?', 'answer': 'Alcane : CₙH₂ₙ₊₂ (liaisons simples) ; Alcène : CₙH₂ₙ (une double liaison C=C) ; Alcyne : CₙH₂ₙ₋₂ (une triple liaison C≡C).', 'hint': 'Hydrocarbures', 'difficulty': 1},
            {'question': 'Qu\'est-ce que le pH d\'une solution ?', 'answer': 'pH = −log[H₃O⁺]. pH<7 : solution acide ; pH=7 : neutre ; pH>7 : basique.', 'hint': 'Mesure l\'acidité', 'difficulty': 1},
        ],
        'physique': [
            {'question': 'Énonce la 2ème loi de Newton.', 'answer': 'ΣF⃗ = m·a⃗ : la somme vectorielle des forces = masse × accélération.', 'hint': 'Principe fondamental de la dynamique', 'difficulty': 1},
            {'question': 'Qu\'est-ce que la loi d\'Ohm ?', 'answer': 'U = R·I  (tension = résistance × intensité). Valide pour un conducteur ohmique en régime permanent.', 'hint': 'Électricité de base', 'difficulty': 1},
            {'question': 'Formule de l\'énergie cinétique ?', 'answer': 'Ec = ½·m·v²  avec m en kg et v en m/s, Ec en Joules.', 'hint': 'Énergie de mouvement', 'difficulty': 2},
            {'question': 'Qu\'est-ce que la loi de Faraday (induction) ?', 'answer': 'e = −dΦ/dt : la force électromotrice induite est égale à l\'opposé de la variation du flux magnétique.', 'hint': 'Induction électromagnétique', 'difficulty': 3},
        ],
        'svt': [
            {'question': 'Quelles sont les deux lois de Mendel ?', 'answer': '1ère loi (uniformité F1) : les hybrides F1 sont uniformes. 2ème loi (ségrégation) : les caractères parentaux réapparaissent en F2 selon un ratio 3:1.', 'hint': 'Génétique mendélienne', 'difficulty': 2},
            {'question': 'Différence entre mitose et méiose ?', 'answer': 'Mitose : 1 cellule → 2 cellules identiques (2n chromosomes) — division cellulaire normale. Méiose : 1 cellule → 4 cellules à n chromosomes — reproduction sexuée.', 'hint': 'Cycles cellulaires', 'difficulty': 2},
            {'question': 'Qu\'est-ce que l\'ADN ?', 'answer': 'Acide DésoxyriboNucléique : molécule en double hélice portant l\'information génétique, composée de nucléotides (A-T, G-C).', 'hint': 'Support de l\'hérédité', 'difficulty': 1},
        ],
        'philosophie': [
            {'question': 'Définir la conscience selon Descartes.', 'answer': 'Pour Descartes, la conscience est la certitude immédiate que l\'esprit a de ses propres états : "Je pense, donc je suis" (Cogito ergo sum).', 'hint': 'Cogito cartésien', 'difficulty': 2},
            {'question': 'Qu\'est-ce que l\'impératif catégorique de Kant ?', 'answer': '"Agis uniquement d\'après la maxime qui te permet de vouloir en même temps qu\'elle devienne une loi universelle." Principe moral absolu, sans condition.', 'hint': 'Éthique kantienne', 'difficulty': 3},
            {'question': 'Liberté et déterminisme sont-ils compatibles ?', 'answer': 'Les compatibilistes (Spinoza, Hume) affirment que oui : la liberté est agir selon sa propre nature. Les incompatibilistes pensent que le déterminisme exclut la liberté.', 'hint': 'Débat classique en philosophie', 'difficulty': 3},
        ],
    },
    # Plan de révision structuré (format attendu par le template)
    'plan_content': {
        'summary': '🎯 Plan personnalisé basé sur tes résultats : priorité à l\'Économie et la SVT, consolidation des Maths, maintien du Français et de la Chimie. 2h de révision quotidienne recommandées.',
        'weeks': [
            {
                'label': 'Sem. 1',
                'focus': 'Économie — Comptabilité nationale & PIB',
                'days': [
                    {'day': 'Lundi', 'subject': 'Économie', 'task': 'Chap. 1 : PIB, PNB et indicateurs macroéconomiques — lecture + fiche mémo', 'duration_min': 90, 'priority': 'high'},
                    {'day': 'Mardi', 'subject': 'SVT', 'task': 'Révision génétique mendélienne : lois + exercices de croisement', 'duration_min': 60, 'priority': 'high'},
                    {'day': 'Mercredi', 'subject': 'Mathématiques', 'task': 'Dérivées : fonctions composées — cours + 10 exercices', 'duration_min': 90, 'priority': 'medium'},
                    {'day': 'Vendredi', 'subject': 'Économie', 'task': 'Fonctions de consommation et d\'épargne — quiz 10 questions', 'duration_min': 60, 'priority': 'high'},
                    {'day': 'Samedi', 'subject': 'Chimie', 'task': 'Oxydoréduction : révision + TD numéros 5-8', 'duration_min': 60, 'priority': 'medium'},
                ],
            },
            {
                'label': 'Sem. 2',
                'focus': 'SVT — Génétique & Division cellulaire',
                'days': [
                    {'day': 'Lundi', 'subject': 'SVT', 'task': 'Mitose vs Méiose : diagrammes + résumé schématique', 'duration_min': 90, 'priority': 'high'},
                    {'day': 'Mardi', 'subject': 'Mathématiques', 'task': 'Intégrales : primitives usuelles + calcul d\'aires', 'duration_min': 90, 'priority': 'medium'},
                    {'day': 'Mercredi', 'subject': 'Philosophie', 'task': 'Chap. Liberté & Déterminisme — plan de dissertation', 'duration_min': 60, 'priority': 'medium'},
                    {'day': 'Jeudi', 'subject': 'SVT', 'task': 'ADN et synthèse des protéines — quiz 15 questions BAC', 'duration_min': 60, 'priority': 'high'},
                    {'day': 'Samedi', 'subject': 'Physique', 'task': 'Cinématique : exercices de trajectoires et vitesses', 'duration_min': 75, 'priority': 'medium'},
                ],
            },
            {
                'label': 'Sem. 3',
                'focus': 'Mathématiques & Physique — Consolidation',
                'days': [
                    {'day': 'Lundi', 'subject': 'Mathématiques', 'task': 'Suites arithmétiques & géométriques — exercices BAC', 'duration_min': 90, 'priority': 'medium'},
                    {'day': 'Mardi', 'subject': 'Physique', 'task': 'Lois de Newton — problèmes de dynamique', 'duration_min': 90, 'priority': 'medium'},
                    {'day': 'Jeudi', 'subject': 'Économie', 'task': 'Politique monétaire et budgétaire — révision + quiz', 'duration_min': 75, 'priority': 'high'},
                    {'day': 'Vendredi', 'subject': 'Français', 'task': 'Commentaire de texte — méthode + texte d\'entraînement', 'duration_min': 90, 'priority': 'low'},
                    {'day': 'Samedi', 'subject': 'Chimie', 'task': 'Thermochimie : enthalpie, loi de Hess', 'duration_min': 60, 'priority': 'medium'},
                ],
            },
        ],
    },
}

# ─────────────────────────────────────────────
# DASHBOARD PRINCIPAL
# ─────────────────────────────────────────────
def dashboard(request):
    # ── Guest / demo mode ──────────────────────────────────────
    if not request.user.is_authenticated:
        if _is_guest(request):
            from types import SimpleNamespace as _SN
            g = _GUEST_DEMO
            _g_stats = _SN(exercices_resolus=6, quiz_completes=3, minutes_etude=135)
            # Pop the pending series flag so modal shows only once
            guest_serie_pending = request.session.pop('guest_serie_pending', False)
            request.session.modified = True
            _serie_choices = [
                ('SVT', '🧬', 'Sciences de la Vie et de la Terre',   'SVT · Chimie · Physique · Maths'),
                ('SMP', '⚗️', 'Sciences Mathématiques et Physiques', 'Maths · Physique · Chimie · SVT'),
                ('SES', '📊', 'Sciences Économiques et Sociales',    'Économie · Histoire · Philo · Maths'),
                ('LLA', '📚', 'Lettres, Langues et Arts',            'Philo · Kreyòl · Anglais · Art'),
            ]
            ctx = {**g, 'mats': MATS, 'profile': None, 'stats': _g_stats, 'is_guest': True,
                   'guest_serie_pending': guest_serie_pending,
                   'serie_choices': _serie_choices}
            return render(request, 'core/dashboard.html', ctx)
        return redirect('/login/?next=' + request.get_full_path())
    _streak_just_earned = _update_streak(request.user)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    stats       = _get_or_create_stats(request.user)

    # Score par matière — blended (quiz+exercices+cours), avec fallback diagnostic
    diag_scores = {d.subject: d.score for d in DiagnosticResult.objects.filter(user=request.user)}
    quiz_scores = {}
    for subj in MATS:
        sc = _compute_subject_blended_score(request.user, subj)
        if sc['blended'] is not None:
            quiz_scores[subj] = sc['blended']
        elif subj in diag_scores:
            quiz_scores[subj] = diag_scores[subj]

    sorted_asc  = sorted(quiz_scores.items(), key=lambda x: x[1])
    sorted_desc = sorted(quiz_scores.items(), key=lambda x: x[1], reverse=True)
    # Seuils : force >= 70%, lacune < 65%
    strengths  = [(s, v) for s, v in sorted_desc if v >= 70][:3]
    weaknesses = [(s, v) for s, v in sorted_asc  if v <  65][:3]

    avg_score = round(sum(quiz_scores.values()) / len(quiz_scores)) if quiz_scores else 0

    # ── Note BAC pondérée par les coefficients officiels ──────────────
    # Chaque matière est pondérée par son coefficient dans la série de l'élève.
    # La note finale est ramenée sur 1900 (total BAC officiel haïtien).
    try:
        _user_serie_key = profile.serie or 'SVT'
        _serie_coeffs   = SERIES.get(_user_serie_key, SERIES['SVT'])['subjects']  # subj → coef
        _weighted_sum   = 0.0
        _total_coeff    = 0
        for _subj, _coef in _serie_coeffs.items():
            if _subj in quiz_scores:
                _weighted_sum += (quiz_scores[_subj] / 100.0) * _coef
                _total_coeff  += _coef
        if _total_coeff > 0:
            # Score pondéré ramené sur 1900
            _total_serie_coeff = sum(_serie_coeffs.values())
            bac_score = round((_weighted_sum / _total_serie_coeff) * 1900)
        else:
            bac_score = round(avg_score / 100 * 1900) if avg_score else 0
    except Exception:
        bac_score = round(avg_score / 100 * 1900) if avg_score else 0

    heures_etude = stats.minutes_etude // 60
    minutes_rest = stats.minutes_etude % 60

    # ── XP / League (Duolingo-style) ──────────────────────────
    def calc_xp(s):
        return s.quiz_completes * 20 + s.exercices_resolus * 50 + s.messages_envoyes * 5

    my_xp = calc_xp(stats)

    # Load users and deduplicate by user_id (avoids ghost duplicates from grace-period sessions)
    _all_stats_qs = (
        UserStats.objects.select_related('user').filter(
            user__is_staff=False,
            user__is_superuser=False,
            user__agent__isnull=True,
        )
    )
    _seen_users = set()
    all_stats = []
    for _s in _all_stats_qs:
        if _s.user_id not in _seen_users:
            _seen_users.add(_s.user_id)
            all_stats.append(_s)
    all_stats.sort(key=lambda s: calc_xp(s), reverse=True)
    global_rank = next((i + 1 for i, s in enumerate(all_stats) if s.user_id == request.user.id), 1)

    # XP-tier based league: every 1000 XP = new league tier
    my_league_tier = my_xp // 1000
    league_slice = [s for s in all_stats if calc_xp(s) // 1000 == my_league_tier][:30]
    league_rank = next((i + 1 for i, s in enumerate(league_slice) if s.user_id == request.user.id), 1)

    # XP milestones for levels
    XP_LEVELS = [0, 100, 250, 500, 1000, 2000, 4000, 8000]
    current_level = sum(1 for lvl in XP_LEVELS if my_xp >= lvl)
    next_lvl_xp = XP_LEVELS[current_level] if current_level < len(XP_LEVELS) else XP_LEVELS[-1]
    prev_lvl_xp = XP_LEVELS[current_level - 1] if current_level > 0 else 0
    xp_progress_pct = min(100, round((my_xp - prev_lvl_xp) / max(1, next_lvl_xp - prev_lvl_xp) * 100)) if next_lvl_xp > prev_lvl_xp else 100

    LEAGUE_NAMES = [
        'Débutant', 'Apprenti', 'Curieux', 'Motivé', 'Travailleur',
        'Persévérant', 'Progressif', 'Déterminé', 'Ambitieux', 'Compétent',
        'Confirmé', 'Performant', 'Avancé', 'Talentueux', 'Expert Junior',
        'Expert', 'Stratège', 'Élite', 'Exceptionnel', 'Impressionnant',
        'Maîtrise', 'Grand Expert', 'Professionnel', 'Leader', 'Champion',
        'Dominant', 'Inarrêtable', 'Légendaire', 'Mythique', 'Maître Absolu',
    ]
    LEAGUE_COLORS = [
        '#78716c', '#d97706', '#f59e0b', '#84cc16', '#22c55e',
        '#10b981', '#14b8a6', '#06b6d4', '#0ea5e9', '#3b82f6',
        '#6366f1', '#8b5cf6', '#a855f7', '#d946ef', '#ec4899',
        '#f43f5e', '#ef4444', '#dc2626', '#b91c1c', '#991b1b',
        '#7c3aed', '#6d28d9', '#5b21b6', '#4c1d95', '#1e3a8a',
        '#1e40af', '#1d4ed8', '#2563eb', '#7c3aed', '#f59e0b',
    ]
    league_name = LEAGUE_NAMES[min(my_league_tier, len(LEAGUE_NAMES) - 1)]
    league_color = LEAGUE_COLORS[min(my_league_tier, len(LEAGUE_COLORS) - 1)]
    next_tier_xp = (my_league_tier + 1) * 1000
    remaining_xp = next_tier_xp - my_xp
    next_league_name = LEAGUE_NAMES[min(my_league_tier + 1, len(LEAGUE_NAMES) - 1)]

    league_data = []
    for i, s in enumerate(league_slice):
        try:
            p = UserProfile.objects.get(user=s.user)
            display = p.first_name or s.user.username
        except Exception:
            display = s.user.username
        league_data.append({
            'rank': i + 1,
            'name': display,
            'xp': calc_xp(s),
            'is_me': s.user_id == request.user.id,
        })

    bac_gap_pass   = max(0, 950 - bac_score)   # points to reach 50% (pass threshold)
    # Second goal: user's personal bac_target if set, otherwise 950 (same as first until set)
    _user_bac_target = getattr(profile, 'bac_target', None) or None
    bac_target_score = _user_bac_target if _user_bac_target and _user_bac_target > 950 else None
    bac_gap_target = max(0, bac_target_score - bac_score) if bac_target_score else None

    _exo_count = stats.exercices_resolus
    _exo_milestones = [5, 10, 25, 50, 100, 200, 500, 1000]
    next_exo_milestone = next((m for m in _exo_milestones if m > _exo_count), 1000)
    exo_milestone_pct  = min(100, round(_exo_count / next_exo_milestone * 100))
    _user_subjs = _get_user_serie_subjects(request.user)
    _weak_in_serie = [(s, v) for s, v in weaknesses if s in _user_subjs]
    exo_focus_subject  = _weak_in_serie[0][0] if _weak_in_serie else None

    # BAC milestones (next objective above current score)
    _bac_milestones = [(950, 'Passable'), (1140, 'Assez bien'), (1330, 'Bien'), (1520, 'Très bien')]
    _bac_now = bac_score or 0
    _next_bac = next(((pts, lab) for pts, lab in _bac_milestones if pts > _bac_now), (1900, 'Mention TB'))
    _prev_bac_pts = max((pts for pts, _ in _bac_milestones if pts <= _bac_now), default=0)
    bac_next_milestone_pts   = _next_bac[0]
    bac_next_milestone_label = _next_bac[1]
    _range = bac_next_milestone_pts - _prev_bac_pts
    bac_milestone_pct = min(100, round((_bac_now - _prev_bac_pts) / _range * 100)) if bac_score and _range > 0 else 0

    context = {
        'profile':         profile,
        'stats':           stats,
        'quiz_scores':     quiz_scores,
        'avg_score':       avg_score,
        'bac_score':       bac_score,
        'bac_gap_pass':    bac_gap_pass,
        'bac_gap_target':  bac_gap_target,
        'bac_target_score': bac_target_score,
        'weaknesses':    weaknesses,
        'strengths':     strengths,
        'mats':          MATS,
        'heures_etude':  heures_etude,
        'minutes_rest':  minutes_rest,
        'recent_sessions': QuizSession.objects.filter(user=request.user).order_by('-completed_at')[:5],
        'my_xp':         my_xp,
        'league_rank':   league_rank,
        'league_name':   league_name,
        'league_color':  league_color,
        'xp_progress_pct': xp_progress_pct,
        'current_level': current_level,
        'league_data':   league_data,
        'global_rank':   global_rank,
        'remaining_xp':  remaining_xp,
        'next_league_name': next_league_name,
        'next_exo_milestone':  next_exo_milestone,
        'exo_milestone_pct':   exo_milestone_pct,
        'exo_focus_subject':   exo_focus_subject,
        'user_serie_subjects': list(_user_subjs),
        'bac_next_milestone_pts':   bac_next_milestone_pts,
        'bac_next_milestone_label': bac_next_milestone_label,
        'bac_milestone_pct':        bac_milestone_pct,
        'bac_prev_milestone_pts':   _prev_bac_pts,
        'streak_just_earned':       _streak_just_earned,
    }

    # Données de maîtrise adaptative pour le dashboard
    try:
        masteries = {
            sm.subject: {
                'mastery': round(sm.mastery_score),
                'confidence': sm.confidence_level,
                'correct': sm.correct_count,
                'errors': sm.error_count,
                'weak_topics': sm.weak_topics[:3],
            }
            for sm in SubjectMastery.objects.filter(user=request.user)
        }
        context['masteries'] = masteries
    except Exception:
        context['masteries'] = {}

    return render(request, 'core/dashboard.html', context)


# ─────────────────────────────────────────────
# CHAT IA
# ─────────────────────────────────────────────

def api_chat_suggestions(request):
    """
    Suggestions aléatoires construites depuis les vrais titres de chapitres/thèmes.
    Évite les formulations trompeuses (pas de nombres d'étapes arbitraires).
    """
    import random
    import re

    try:
        from .resource_index import get_subject_chapters, get_all_exam_themes, get_quiz_categories
    except Exception:
        get_subject_chapters = None
        get_all_exam_themes = None
        get_quiz_categories = None

    user = request.user if request.user.is_authenticated else None
    user_subjs = _get_user_serie_subjects(user) if user else set(MATS.keys())

    def _clean_topic(raw: str) -> str:
        t = (raw or '').strip()
        t = re.sub(r'^(chapitre|chap\.?|section|partie)\s*\d+\s*[:\-–—]?\s*', '', t, flags=re.IGNORECASE)
        t = re.sub(r'\s+', ' ', t).strip(' .;:,-')
        return t

    def _make_question(subject: str, topic: str) -> str:
        topic = _clean_topic(topic)
        if not topic:
            return ''

        templates = {
            'francais': [
                'Nan chapit "{topic}", ki pwen kle yo mwen dwe metrize pou egzamen BAC la?',
                'Ban mwen yon eksplikasyon klè sou "{topic}" ak yon egzanp kout.',
                'Ki erè elèv yo fè souvan sou "{topic}" epi kijan pou m evite yo?',
            ],
            'anglais': [
                'Can you explain "{topic}" with one BAC-style example and a short correction?',
                'What are the most common BAC mistakes on "{topic}", and how can I avoid them?',
                'Give me a focused revision question on "{topic}" with a model answer.',
            ],
            'espagnol': [
                'Explícame "{topic}" con un ejemplo tipo BAC y una corrección breve.',
                '¿Cuáles son los errores más comunes sobre "{topic}" en el BAC?',
                'Dame una pregunta de repaso sobre "{topic}" con respuesta modelo.',
            ],
        }
        default_templates = [
            'Explique clairement "{topic}" avec un exemple type BAC.',
            'Quelles sont les erreurs fréquentes sur "{topic}" et comment les éviter ?',
            'Propose une question d\'entraînement BAC sur "{topic}" avec correction rapide.',
        ]

        bank = templates.get(subject, default_templates)
        return random.choice(bank).format(topic=topic)

    suggestions = {
        'general': [
            'Donne-moi une méthode fiable pour analyser un sujet BAC avant de rédiger la réponse.',
            'Aide-moi à identifier mes erreurs les plus fréquentes et à construire une fiche de révision utile.',
            'Propose un mini entraînement mixte basé sur les thèmes prioritaires de ma série.',
        ]
    }

    for subj in MATS:
        if subj not in user_subjs:
            continue

        topics = []
        if get_subject_chapters:
            try:
                topics.extend(get_subject_chapters(subj) or [])
            except Exception:
                pass
        if get_all_exam_themes:
            try:
                topics.extend(get_all_exam_themes(subj) or [])
            except Exception:
                pass
        if get_quiz_categories:
            try:
                topics.extend(get_quiz_categories(subj) or [])
            except Exception:
                pass

        cleaned = []
        seen = set()
        for t in topics:
            c = _clean_topic(str(t))
            if not c:
                continue
            k = c.lower()
            if k in seen:
                continue
            seen.add(k)
            cleaned.append(c)

        random.shuffle(cleaned)
        picked = cleaned[:6]
        qs = []
        for p in picked:
            q = _make_question(subj, p)
            if q:
                qs.append(q)
            if len(qs) >= 3:
                break

        # Fallback sûr si aucune donnée exploitable
        if not qs:
            label = MATS.get(subj, {}).get('label', subj)
            qs = [
                f'Quel est le noyau du programme BAC en {label} à réviser en priorité ?',
                f'Donne-moi un exercice type BAC en {label} avec correction guidée.',
                f'Quelles erreurs font perdre le plus de points en {label} au BAC ?',
            ]

        suggestions[subj] = qs[:3]

    return JsonResponse({'ok': True, 'suggestions': suggestions})


# ── Demo Q&A (réponses réelles stockées en DB, visibles par tous les visiteurs) ──

_DEMO_QA_QUESTIONS = {
    'svt':         "Explique la différence entre la mitose et la méiose, en soulignant leurs rôles dans l'organisme.",
    'maths':       "Comment calculer la dérivée de f(x) = x³ + 2x² − 5x + 1 et trouver ses points critiques ?",
    'physique':    "Comment appliquer la 2ᵉ loi de Newton pour résoudre un problème de plan incliné avec frottement ?",
    'chimie':      "Explique le principe d'une réaction d'oxydoréduction et donne un exemple concret.",
    'philosophie': "En quoi consiste l'impératif catégorique de Kant et comment s'applique-t-il dans la vie quotidienne ?",
    'francais':    "Ki diferans ki genyen ant yon tèks naratif ak yon tèks agimantativ ? Bay egzanp chak.",
    'anglais':     "What are the main challenges Haiti faces today, and what solutions could help address them?",
    'histoire':    "Quelles sont les principales conséquences de l'occupation américaine d'Haïti (1915-1934) ?",
    'economie':    "Comment mesure-t-on le PIB d'un pays et quelle est sa différence avec le PNB ?",
}


@require_POST
def api_refresh_demo_qa(request):
    """Admin-only: (re)generate a public demo Q&A via the real AI for a given matiere."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({'ok': False, 'error': 'Réservé aux administrateurs'}, status=403)
    try:
        data    = json.loads(request.body)
        matiere = str(data.get('matiere', '')).lower().strip()
    except (ValueError, KeyError):
        return JsonResponse({'ok': False, 'error': 'Requête invalide'}, status=400)

    question = _DEMO_QA_QUESTIONS.get(matiere)
    if not question:
        return JsonResponse({'ok': False, 'error': f'Matière inconnue : {matiere}'}, status=400)

    try:
        answer = gemini.get_chat_response(
            message=question, history=[], subject=matiere,
            db_context='', user_profile=None, user_lang='fr',
        )
        from .models import PublicDemoQA
        PublicDemoQA.objects.update_or_create(
            matiere=matiere,
            defaults={'question': question, 'answer': answer},
        )
        return JsonResponse({'ok': True, 'matiere': matiere, 'preview': answer[:120]})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


def chat_view(request):
    if not request.user.is_authenticated:
        if _is_guest(request):
            from .models import PublicDemoQA
            demo_qa = list(
                PublicDemoQA.objects.values('matiere', 'question', 'answer', 'updated_at')
            )
            return render(request, 'core/chat.html', {
                'mats': MATS,
                'conversations': [],
                'preload_subject': '',
                'preload_message': '',
                'preload_session': '',
                'is_guest': True,
                'demo_qa': demo_qa,
            })
        return redirect('/login/?next=' + request.get_full_path())
    # Recent conversations pour le panel historique
    from django.db.models import Max, Count
    conversations = (
        ChatMessage.objects.filter(user=request.user)
        .exclude(session_key='')
        .values('session_key', 'subject')
        .annotate(last_msg=Max('created_at'), msg_count=Count('id'))
        .order_by('-last_msg')
    )
    # Ajouter le premier message de chaque conversation pour le titre
    conv_list = []
    for conv in conversations:
        first_msg = ChatMessage.objects.filter(
            user=request.user, session_key=conv['session_key'], role='user'
        ).order_by('created_at').first()
        conv_list.append({
            'session_key': conv['session_key'],
            'subject': conv['subject'],
            'preview': (first_msg.content[:60] + '…') if first_msg and len(first_msg.content) > 60 else (first_msg.content if first_msg else 'Conversation'),
            'last_msg': conv['last_msg'],
        })
    # Filtrer les matières selon la série du user
    user_subjs = _get_user_serie_subjects(request.user)
    chat_mats = {k: v for k, v in MATS.items() if k in user_subjs}
    # Check premium status & remaining chat messages
    from core.premium import is_premium as _is_prem, can_use_chat
    user_is_premium = _is_prem(request.user)
    _, chat_remaining = can_use_chat(request.user)

    return render(request, 'core/chat.html', {
        'mats': chat_mats,
        'conversations': conv_list,
        'preload_subject': request.GET.get('subject', ''),
        'preload_message': request.GET.get('preload', ''),
        'preload_session': request.GET.get('session', ''),
        'is_premium': user_is_premium,
        'chat_remaining': chat_remaining,
        'pdf_path': request.GET.get('pdf', ''),
        'pdf_name': request.GET.get('pdf_name', ''),
        'pdf_text': request.GET.get('pdf_text', ''),
    })


@require_POST
def chat_api(request):
    import uuid
    import traceback

    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Session expiree. Reconnecte-toi pour continuer.'}, status=401)

    # ── Premium gate: 2 messages/jour gratuits ──
    from core.premium import can_use_chat, increment_chat, premium_required_json
    allowed, remaining = can_use_chat(request.user)
    if not allowed:
        return JsonResponse(premium_required_json(), status=403)
    
    try:
        text = request.POST.get('message', '').strip()
        subject = (request.POST.get('subject', '') or '').strip().lower()
        session_key = request.POST.get('session_key', '') or uuid.uuid4().hex[:16]
        image = request.FILES.get('image')

        if not text:
            return JsonResponse({'error': 'Message vide.'}, status=400)

        if not subject or subject == 'general':
            return JsonResponse({'error': 'Choisis d abord une matiere.'}, status=400)

        if subject not in MATS:
            return JsonResponse({'error': 'Matiere invalide.'}, status=400)

        image_data = None
        image_mime = None
        try:
            image_data = image.read() if image else None
            image_mime = image.content_type if image else None
        except Exception:
            image_data = None
            image_mime = None

        # 1) Recherche locale: on construit un contexte fiable depuis les JSON.
        result = ''
        try:
            result = _search_ai_blocks(subject, chapter_num=0, query=text, max_blocks=8)
        except Exception as block_err:
            print(f"[AI_BLOCK_SEARCH_ERROR] {subject}: {block_err}")
            result = ''

        if not result:
            try:
                result = _get_db_context(subject, user_message=text)
            except Exception as ctx_err:
                print(f"[DB_CONTEXT_ERROR] {subject}: {ctx_err}")
                result = ''

        local_context = ''
        local_fallback_reply = ''

        # If the user asks for an exercise/example, build a compact targeted local context.
        best_exo = _pick_best_local_exercise_block(subject, text)
        if best_exo:
            chapter = best_exo.get('chapter', '').strip() or 'Chapitre non precise'
            subchapter = best_exo.get('subchapter', '').strip() or 'Sous-chapitre non precise'
            content = (best_exo.get('content', '') or '').strip()
            if len(content) > 2400:
                content = content[:2400].rstrip() + '\n\n...'
            local_context = (
                f"Chapitre: {chapter}\n"
                f"Sous-chapitre: {subchapter}\n"
                f"Exercice local:\n{content}\n"
            )
            local_fallback_reply = (
                f"### Exercice type - {MATS.get(subject, {}).get('label', subject)}\n\n"
                f"**Chapitre:** {_escape_markdown_text(chapter)}\n"
                f"**Sous-chapitre:** {_escape_markdown_text(subchapter)}\n\n"
                f"{_escape_markdown_text(content)}\n\n"
                "_Source: notes JSON locales_"
            )
        elif result:
            snippet = result.strip()
            if len(snippet) > 9000:
                snippet = snippet[:9000].rstrip() + '\n\n...'
            local_context = snippet
            local_fallback_reply = (
                f"### Recherche locale: {MATS.get(subject, {}).get('label', subject)}\n\n"
                "Voici les passages les plus pertinents trouves dans les notes JSON:\n\n"
                f"{_escape_markdown_text(snippet)}"
            )
        else:
            local_fallback_reply = (
                f"Je n ai pas trouve de passage pertinent dans les notes JSON de {MATS.get(subject, {}).get('label', subject)}. "
                "Essaie avec des mots-cles plus precis."
            )

        # 2) Appel IA avec question + contexte local trouvé.
        reply = ''
        if local_context:
            history = []
            try:
                history_qs = ChatMessage.objects.filter(
                    user=request.user,
                    subject=subject,
                    session_key=session_key,
                ).order_by('-created_at')[:20]
                for msg in reversed(list(history_qs)):
                    role = 'model' if msg.role == 'ai' else 'user'
                    history.append({'role': role, 'parts': [msg.content]})
            except Exception:
                history = []

            user_profile = None
            try:
                user_profile = gemini.build_user_learning_profile_short(request.user)
            except Exception:
                user_profile = None

            user_lang = _get_user_lang(request)

            # Keep local context compact for cost/stability.
            ai_db_context = local_context[:12000]

            try:
                ai_reply = gemini.get_chat_response(
                    message=text,
                    history=history,
                    subject=subject,
                    db_context=ai_db_context,
                    image_data=image_data,
                    image_mime=image_mime,
                    user_profile=user_profile,
                    user_lang=user_lang,
                )
                if ai_reply and ai_reply.strip():
                    reply = ai_reply.strip()
            except Exception as ai_err:
                print(f"[CHAT_GEMINI_ERROR] {subject}: {ai_err}")

        if not reply:
            reply = local_fallback_reply

        print(f"[CHAT_HYBRID] subject={subject} query={text[:60]} reply_len={len(reply)}")

        # SAUVEGARDER l'échange
        try:
            if text:
                ChatMessage.objects.create(user=request.user, role='user', content=text, subject=subject, session_key=session_key)
            ChatMessage.objects.create(user=request.user, role='ai', content=reply, subject=subject, session_key=session_key)
            # Incrémenter le compteur chat gratuit
            increment_chat(request.user)
        except Exception as save_err:
            print(f"[SAVE_ERROR] {str(save_err)}\n{traceback.format_exc()}")
            # Continue même si la sauvegarde échoue

        return JsonResponse({'reply': reply, 'followups': [], 'session_key': session_key})

    except Exception as e:
        _logger.exception('Server error')
        error_msg = 'Erreur interne du serveur.'
        print(f"[CHAT_ERROR] {error_msg}\n{traceback.format_exc()}")
        return JsonResponse({'error': error_msg}, status=500)


@login_required
def api_load_session(request):
    """Retourne tous les messages d'une session existante pour restaurer une conversation."""
    session_key = request.GET.get('session_key', '').strip()
    if not session_key:
        return JsonResponse({'error': 'session_key requis'}, status=400)
    msgs = ChatMessage.objects.filter(
        user=request.user, session_key=session_key
    ).order_by('created_at')
    if not msgs.exists():
        return JsonResponse({'error': 'session introuvable'}, status=404)
    data = []
    for m in msgs:
        data.append({
            'role':    m.role,
            'content': m.content,
            'created_at': m.created_at.strftime('%H:%M'),
        })
    subject = msgs.first().subject
    return JsonResponse({'messages': data, 'subject': subject, 'session_key': session_key})


_NOTE_FILES_MAP = {
    'physique':    'note_physique.json',
    'maths':       'note_math.json',
    'chimie':      'note_de_Chimie.json',
    'svt':         'note_SVT.json',
    'francais':    'note_kreyol.json',
    'philosophie': 'note_philosophie.json',
    'anglais':     'note_anglais.json',
    'espagnol':    'note_espagnol.json',
    'economie':    'note_economie.json',
    'informatique':'note_informatique.json',
    'histoire':    'note_histoire.json',
    'art':         'note_art.json',
}


def _extract_relevant_note_section(note_text: str, query: str, max_chars: int = 5000) -> str:
    """Extrait la section la plus pertinente du fichier note pour la question posée.
    Stratégie : score chaque fenêtre de `max_chars` et retourne celle avec le plus
    de hits sur les mots-clés. Fallback : début du fichier.
    """
    if not note_text or not query:
        return note_text[:max_chars] if note_text else ''

    import re as _re_note
    _stop = {'quoi', 'quel', 'quelle', 'comment', 'pour', 'avec', 'dans', 'sont', 'the', 'what', 'and', 'cette', 'cest', 'est'}
    keywords = [w for w in _re_note.findall(r'\b\w{3,}\b', query.lower()) if w not in _stop]

    if not keywords:
        return note_text[:max_chars]

    note_lower = note_text.lower()

    # Collect all keyword match positions
    positions = []
    for kw in keywords:
        for m in _re_note.finditer(_re_note.escape(kw), note_lower):
            positions.append(m.start())

    if not positions:
        return note_text[:max_chars]

    # Score each candidate start: count how many keyword hits fall within [start, start+max_chars]
    # Candidates = each match position, shifted back to start of the nearest section break
    best_start = 0
    best_score = -1
    candidates = sorted(set(max(0, p - 200) for p in positions))

    for cand in candidates:
        # Snap to nearest section break (double newline) before candidate
        snap = cand
        search_from = max(0, cand - 400)
        last_break = note_text.rfind('\n\n', search_from, cand)
        if last_break != -1:
            snap = last_break + 2
        window_end = snap + max_chars
        score = sum(1 for p in positions if snap <= p < window_end)
        if score > best_score:
            best_score = score
            best_start = snap

    chunk = note_text[best_start: best_start + max_chars]
    return chunk.strip()


def _get_db_context(subject, user_message: str = ''):
    """
    Retourne du contenu de cours depuis :
      1. Les notes officielles du programme (note_*.json) — prioritaire
      2. Les PDFs d'examens indexés
      3. Les exercices en base

    OPTIMISATION COÛT :
    - On ne charge le contexte QUE si le message est assez long/spécifique
    - Questions < 40 chars ou salutations → pas de contexte (économie de tokens)
    """
    # Auto-detect subject from keywords when subject='general'
    _SUBJ_KEYWORDS = {
        'histoire':    ['vincent', 'stenio', 'dessalines', 'toussaint', 'haiti', 'haïti', 'revolution', 'revolution', 'independance', 'présiden', 'presiden', 'roi', 'empire', 'guerre', 'colonie', 'esclave', 'christophe', 'petion', 'boyer', 'estimé', 'magloire', 'duvalier', 'aristide', 'preval', 'martelly', 'moïse', 'moise', 'saint-domingue'],
        'maths':       ['derive', 'dérive', 'intégral', 'integral', 'limite', 'équation', 'equation', 'probabilit', 'matrice', 'vecteur', 'trigono', 'logarithm', 'fonction'],
        'physique':    ['newton', 'force', 'vitesse', 'accélér', 'acceler', 'energie', 'énergie', 'courant', 'tension', 'circuit', 'optique', 'lumière', 'lumiere', 'onde'],
        'chimie':      ['ph', 'acide', 'base', 'oxydor', 'molécule', 'molecule', 'réaction', 'reaction', 'atome', 'élément', 'element'],
        'svt':         ['cellule', 'adn', 'gène', 'gene', 'mendel', 'photosynthèse', 'photosynth', 'chromosom', 'mitose', 'méiose', 'meiose', 'écosystème', 'ecosysteme'],
        'philosophie': ['conscience', 'liberté', 'liberte', 'hobbes', 'rousseau', 'platon', 'socrate', 'aristote', 'descartes', 'kant', 'nietzsche', 'existential', 'philosophie', 'philosophique', 'philo', 'étude de texte', 'etude de texte', 'commentaire de texte', 'mythe', 'état de nature', 'contrat social', 'dialectique'],
        'anglais':     ['present perfect', 'preterite', 'tense', 'passive', 'essay', 'grammar', 'question tag', 'reported speech', 'conditional'],
        'francais':    ['dissertation', 'poème', 'poeme', 'figure de style', 'métaphore', 'metaphore', 'romantisme', 'réalisme', 'realisme', 'commentaire', 'résumé', 'resume', 'texte littéraire', 'recit', 'récit', 'narrat'],
        'economie':    ['pib', 'inflation', 'marché', 'marche', 'offre', 'demande', 'monnaie', 'banque', 'commerce'],
    }
    if not subject or subject == 'general':
        # Try to auto-detect subject from message keywords
        msg_lower_tmp = user_message.strip().lower()
        detected = None
        for _s, _kws in _SUBJ_KEYWORDS.items():
            if any(kw in msg_lower_tmp for kw in _kws):
                detected = _s
                break
        if not detected:
            # No specific subject detected — load condensed notes from ALL subjects as fallback
            _SKIP_GEN = {'bonjou', 'bonswa', 'salut', 'alo', 'ok', 'merci', 'mesi', 'dako', 'super', 'hi', 'hello', 'bye', 'au revoir'}
            _msg_tmp = user_message.strip().lower()
            if len(_msg_tmp) < 10 or _msg_tmp in _SKIP_GEN:
                return ''
            from pathlib import Path as _NPathAll
            import json as _jsonAll
            _db_base_all = _NPathAll(__file__).resolve().parent.parent / 'database'
            _all_parts = []
            for _s_all, _fname_all in _NOTE_FILES_MAP.items():
                try:
                    _np_all = _db_base_all / _fname_all
                    if not _np_all.exists():
                        continue
                    _raw_all = _np_all.read_text(encoding='utf-8')
                    try:
                        _obj_all = _jsonAll.loads(_raw_all)
                        if isinstance(_obj_all, dict) and 'raw_text' in _obj_all:
                            _raw_all = _obj_all['raw_text']
                    except _jsonAll.JSONDecodeError:
                        pass
                    _sec_all = _extract_relevant_note_section(_raw_all, user_message, max_chars=800)
                    if _sec_all:
                        _all_parts.append(f"=== {_s_all.upper()} ===\n{_sec_all}")
                except Exception:
                    pass
            return ("[Notes du programme BAC — toutes matières]\n\n" + "\n\n".join(_all_parts)) if _all_parts else ''
        subject = detected

    # Questions trop courtes → pas la peine de charger du contexte
    _NO_CONTEXT_THRESHOLD = 15
    _SKIP_WORDS = {'bonjou', 'bonswa', 'salut', 'alo', 'ok', 'merci', 'mesi', 'dako', 'super'}
    msg_lower = user_message.strip().lower()
    if len(msg_lower) < _NO_CONTEXT_THRESHOLD or msg_lower in _SKIP_WORDS:
        return ''

    from pathlib import Path as _NotePath

    # 0. Notes officielles du programme BAC (note_*.json) — section la plus pertinente
    note_context = ''
    
    # Use atomized AI blocks (better search) for calculation subjects;
    # fallback to raw text extraction for non-calculation subjects
    if subject not in _NO_JSON_CONTEXT_SUBJECTS:
        try:
            # Try AI blocks first (chapter_num=0 = all chapters)
            note_context = _search_ai_blocks(subject, chapter_num=0, query=user_message, max_blocks=12)
        except Exception as _ai_err:
            print(f"[AI_BLOCKS_ERROR] {subject}: {_ai_err}")
            note_context = ''
    
    # Fallback to raw text extraction if no AI blocks available (or for non-calculation subjects)
    if not note_context:
        _note_filename = _NOTE_FILES_MAP.get(subject)
        if _note_filename:
            try:
                import json as _json_note
                _note_path = _NotePath(__file__).resolve().parent.parent / 'database' / _note_filename
                _note_raw = _note_path.read_text(encoding='utf-8')
                # If the file is a JSON with a 'raw_text' key, extract it
                try:
                    _note_obj = _json_note.loads(_note_raw)
                    if isinstance(_note_obj, dict) and 'raw_text' in _note_obj:
                        _note_raw = _note_obj['raw_text']
                except _json_note.JSONDecodeError:
                    pass  # plain text file, use as-is
                # For 'francais' (Kreyòl), expand French query terms to their Kreyòl note equivalents
                _note_query = user_message
                if subject == 'francais':
                    _kr_synonyms = {
                        'dissertation': 'pwodiksyon agimantatif tèks ekri',
                        'commentaire':  'konpreyansyon tèks',
                        'rédaction':    'pwodiksyon ekri',
                        'résumé':       'rezime',
                        'analyse':      'analiz tèks',
                        'texte':        'tèks',
                        'grammaire':    'gramè',
                        'figure de style': 'estil',
                        'narrat':       'naratif resi',
                    }
                    _qry_l = user_message.lower()
                    _extras = [_kr for _fr, _kr in _kr_synonyms.items() if _fr in _qry_l]
                    if _extras:
                        _note_query = user_message + ' ' + ' '.join(_extras)
                note_context = _extract_relevant_note_section(_note_raw, _note_query, max_chars=5000)
            except Exception as _ne:
                print(f"[NOTE_LOAD_ERROR] {subject}: {_ne}")

    # 1. Contenu extrait des PDFs de cours (limité à 1500 chars si on a déjà des notes)
    _pdf_max = 1500 if note_context else 2000
    pdf_context = pdf_loader.get_course_context(subject, max_chars=_pdf_max)

    # 2. Exercices déjà en base (enrichissement complémentaire)
    qs = QuizQuestion.objects.filter(subject=subject)[:4]
    db_lines = []
    for q in qs:
        db_lines.append(f"[Exercice] {q.enonce}" + (f" → {q.explication}" if q.explication else ''))
    db_context = '\n'.join(db_lines)

    parts = []
    if note_context:
        parts.append(f"[Notes officielles du programme BAC — {subject}]\n{note_context}")
    if pdf_context:
        parts.append(pdf_context)
    if db_lines:
        parts.append(db_context)
    return '\n\n'.join(parts)


# ─────────────────────────────────────────────
# QUIZ
# ─────────────────────────────────────────────
def quiz_view(request):
    if not request.user.is_authenticated:
        if _is_guest(request):
            return render(request, 'core/quiz.html', {'mats': MATS, 'is_guest': True})
        return redirect('/login/?next=' + request.get_full_path())
    user_subjs = _get_user_serie_subjects(request.user)
    filtered_mats = {k: v for k, v in MATS.items() if k in user_subjs}

    from core.premium import is_premium as _is_prem, can_use_quiz
    user_is_premium = _is_prem(request.user)
    _, quiz_remaining = can_use_quiz(request.user)

    return render(request, 'core/quiz.html', {
        'mats': filtered_mats,
        'is_premium': user_is_premium,
        'quiz_remaining': quiz_remaining,
    })


def _normalize_text_for_match(text: str) -> str:
    text = (text or '').strip().lower()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return text.strip()


def _verify_extra_bet_submission_with_ai(subject: str, question_type: str, prompt: str, answer: str, options: list) -> dict:
    """AI fact-check before publication for community-created study items."""
    try:
        options_txt = '\n'.join([f"- {str(o)}" for o in (options or [])]) if options else '(none)'
        ai_prompt = (
            "Tu es un verificateur academique strict pour le BAC haitien. "
            "Analyse si la question et sa reponse sont factuellement justes et pedagogiquement valides.\n\n"
            f"Matiere: {subject}\n"
            f"Type: {question_type}\n"
            f"Question: {prompt}\n"
            f"Reponse proposee: {answer}\n"
            f"Options: {options_txt}\n\n"
            "Reponds UNIQUEMENT en JSON valide avec ce schema:\n"
            "{\"valid\": true|false, \"reason\": \"...\", \"correct_answer\": \"...\"}"
        )
        raw = gemini._call_json(ai_prompt, max_tokens=450)
        m = re.search(r'\{[\s\S]*\}', raw or '')
        if not m:
            return {'valid': True, 'reason': 'Verification automatique indisponible.', 'correct_answer': answer}
        parsed = json.loads(m.group(0))
        return {
            'valid': bool(parsed.get('valid', True)),
            'reason': str(parsed.get('reason', '') or 'Validation terminee.'),
            'correct_answer': str(parsed.get('correct_answer', '') or answer),
        }
    except Exception:
        return {'valid': True, 'reason': 'Verification automatique indisponible.', 'correct_answer': answer}


def extra_bet_view(request):
    # ── Guest mode: allowed to browse and interact but can't publish ──
    if _is_guest(request):
        from django.db.models import Count, Q
        posts_qs = ExtraBetPost.objects.select_related('user', 'user__profile').annotate(
            likes_count=Count('likes', distinct=True),
            attempts_count=Count('attempts', distinct=True),
            correct_count=Count('attempts', filter=Q(attempts__is_correct=True), distinct=True),
            user_liked=Count('likes', filter=Q(likes=None), distinct=True),  # always 0 for guests
        )
        active_subject = (request.GET.get('subject') or '').strip().lower()
        sort = (request.GET.get('sort') or 'recent').strip().lower()
        sort_map = {'recent': '-created_at', 'likes': '-likes_count', 'unanswered': 'attempts_count'}
        posts_qs = posts_qs.order_by(sort_map.get(sort, '-created_at'))
        posts = list(posts_qs[:120])
        subject_labels = json.dumps({k: v['label'] for k, v in MATS.items()})
        subject_colors = json.dumps({k: v['color'] for k, v in MATS.items()})
        top_creators = (
            ExtraBetPost.objects
            .values('user__id', 'user__username', 'user__profile__first_name')
            .annotate(post_count=Count('id'), total_likes=Count('likes'))
            .order_by('-post_count')[:5]
        )
        return render(request, 'core/extra_bet.html', {
            'mats': MATS,
            'posts': posts,
            'active_subject': active_subject,
            'active_sort': sort,
            'subject_labels': subject_labels,
            'subject_colors': subject_colors,
            'top_creators': list(top_creators),
            'current_user_id': None,
            'is_guest': True,
        })

    # ── Authenticated: premium gate ──
    from core.premium import is_premium
    if not is_premium(request.user):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        return render(request, 'core/premium_required.html', {
            'profile': profile,
            'feature': 'Extra bèt',
            'message': 'La section Extra bèt est réservée aux abonnés premium. Upgrade pour défier la communauté !',
        })

    from django.db.models import Count, Q, Avg, Case, When, FloatField
    user_subjs = _get_user_serie_subjects(request.user)
    filtered_mats = {k: v for k, v in MATS.items() if k in user_subjs}
    active_subject = (request.GET.get('subject') or '').strip().lower()
    sort = (request.GET.get('sort') or 'recent').strip().lower()

    posts_qs = ExtraBetPost.objects.select_related('user', 'user__profile').annotate(
        likes_count=Count('likes', distinct=True),
        attempts_count=Count('attempts', distinct=True),
        correct_count=Count('attempts', filter=Q(attempts__is_correct=True), distinct=True),
        user_liked=Count('likes', filter=Q(likes=request.user)),
    )
    if active_subject and active_subject in filtered_mats:
        posts_qs = posts_qs.filter(subject=active_subject)

    sort_map = {
        'recent': '-created_at',
        'likes': '-likes_count',
        'unanswered': 'attempts_count',
    }
    posts_qs = posts_qs.order_by(sort_map.get(sort, '-created_at'))

    posts = list(posts_qs[:120])
    # Build subject label map for template
    subject_labels = json.dumps({k: v['label'] for k, v in MATS.items()})
    subject_colors = json.dumps({k: v['color'] for k, v in MATS.items()})
    # Leaderboard: top creators by number of posts
    top_creators = (
        ExtraBetPost.objects
        .values('user__id', 'user__username', 'user__profile__first_name')
        .annotate(post_count=Count('id'), total_likes=Count('likes'))
        .order_by('-post_count')[:5]
    )
    return render(request, 'core/extra_bet.html', {
        'mats': filtered_mats,
        'posts': posts,
        'active_subject': active_subject,
        'active_sort': sort,
        'subject_labels': subject_labels,
        'subject_colors': subject_colors,
        'top_creators': list(top_creators),
        'current_user_id': request.user.id,
    })


@login_required
@require_POST
def api_extra_bet_create(request):
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Payload invalide.'}, status=400)

    subject = str(data.get('subject', '')).strip().lower()
    question_type = str(data.get('question_type', 'direct')).strip().lower()
    prompt = str(data.get('prompt', '')).strip()
    answer = str(data.get('answer', '')).strip()
    options = data.get('options', []) or []
    if not isinstance(options, list):
        options = []
    options = [str(o).strip() for o in options if str(o).strip()]

    allowed_types = {'direct', 'fill', 'qcm'}
    if subject not in MATS:
        return JsonResponse({'ok': False, 'error': 'Matiere invalide.'}, status=400)
    if question_type not in allowed_types:
        return JsonResponse({'ok': False, 'error': 'Type de question invalide.'}, status=400)
    if len(prompt) < 12 or len(answer) < 1:
        return JsonResponse({'ok': False, 'error': 'Question/reponse trop courte.'}, status=400)
    if question_type == 'qcm' and len(options) < 2:
        return JsonResponse({'ok': False, 'error': 'Le QCM doit contenir au moins 2 options.'}, status=400)

    verdict = _verify_extra_bet_submission_with_ai(subject, question_type, prompt, answer, options)
    if not verdict.get('valid', True):
        return JsonResponse({
            'ok': False,
            'valid': False,
            'error': verdict.get('reason') or 'La reponse proposee ne semble pas correcte.',
            'correct_answer': verdict.get('correct_answer') or answer,
        }, status=200)

    post = ExtraBetPost.objects.create(
        user=request.user,
        subject=subject,
        question_type=question_type,
        prompt=prompt,
        answer=answer,
        options=options,
        ai_verdict=verdict,
    )

    return JsonResponse({
        'ok': True,
        'post_id': post.id,
        'message': 'Publication validee et publiee.',
    }, status=201)


@require_POST
def api_extra_bet_answer(request):
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Payload invalide.'}, status=400)

    post_id = data.get('post_id')
    submitted = str(data.get('answer', '')).strip()
    if not post_id or not submitted:
        return JsonResponse({'ok': False, 'error': 'Reponse manquante.'}, status=400)

    try:
        post = ExtraBetPost.objects.get(id=post_id)
    except ExtraBetPost.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Publication introuvable.'}, status=404)

    expected = str(post.answer or '').strip()
    is_correct = False
    if post.question_type == 'qcm':
        opts = post.options or []
        s_norm = _normalize_text_for_match(submitted)
        e_norm = _normalize_text_for_match(expected)
        if len(s_norm) == 1 and s_norm in 'abcd':
            is_correct = s_norm == e_norm
        elif len(e_norm) == 1 and e_norm in 'abcd':
            expected_idx = ord(e_norm) - ord('a')
            if 0 <= expected_idx < len(opts):
                is_correct = s_norm == _normalize_text_for_match(str(opts[expected_idx]))
        else:
            is_correct = s_norm == e_norm
    else:
        s_norm = _normalize_text_for_match(submitted)
        e_norm = _normalize_text_for_match(expected)
        is_correct = bool(s_norm) and bool(e_norm) and (s_norm == e_norm or s_norm in e_norm or e_norm in s_norm)

    # Guests: return result without recording attempt in DB
    if not _is_guest(request) and request.user.is_authenticated:
        ExtraBetAttempt.objects.update_or_create(
            post=post,
            user=request.user,
            defaults={
                'submitted_answer': submitted,
                'is_correct': is_correct,
            },
        )
    responders_count = ExtraBetAttempt.objects.filter(post=post).values('user').distinct().count()
    creators_count = ExtraBetPost.objects.filter(subject=post.subject).values('user').distinct().count()

    return JsonResponse({
        'ok': True,
        'is_correct': is_correct,
        'correct_answer': expected,
        'responders_count': responders_count,
        'creators_count': creators_count,
    })


@require_POST
def api_extra_bet_ai_help(request):
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Payload invalide.'}, status=400)

    post_id = data.get('post_id')
    user_msg = str(data.get('message', '')).strip()
    if not post_id or not user_msg:
        return JsonResponse({'ok': False, 'error': 'Question IA manquante.'}, status=400)

    try:
        post = ExtraBetPost.objects.get(id=post_id)
    except ExtraBetPost.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Publication introuvable.'}, status=404)

    preface = (
        "Aide l eleve sur cette question sans donner directement toute la solution en une seule ligne. "
        "Explique clairement, par etapes, avec un style pedagogique.\n\n"
        f"Question: {post.prompt}\n"
        f"Type: {post.question_type}\n"
        f"Reponse correcte: {post.answer}\n"
        f"Options: {post.options}\n\n"
        f"Question de l eleve: {user_msg}"
    )
    reply = gemini.get_chat_response(preface, history=[], subject=post.subject, db_context='')
    return JsonResponse({'ok': True, 'reply': reply})


@require_POST
def api_extra_bet_like(request):
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Payload invalide.'}, status=400)

    post_id = data.get('post_id')
    if not post_id:
        return JsonResponse({'ok': False, 'error': 'Post ID manquant.'}, status=400)

    try:
        post = ExtraBetPost.objects.get(id=post_id)
    except ExtraBetPost.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Publication introuvable.'}, status=404)

    # Guests: simulate like animation without DB write
    if _is_guest(request) or not request.user.is_authenticated:
        likes_count = post.likes.count()
        return JsonResponse({'ok': True, 'liked': True, 'likes_count': likes_count + 1})

    if post.likes.filter(id=request.user.id).exists():
        post.likes.remove(request.user)
        liked = False
    else:
        post.likes.add(request.user)
        liked = True

    return JsonResponse({
        'ok': True,
        'liked': liked,
        'likes_count': post.likes.count(),
    })


@login_required
@require_POST
def api_extra_bet_delete(request):
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Payload invalide.'}, status=400)

    post_id = data.get('post_id')
    if not post_id:
        return JsonResponse({'ok': False, 'error': 'Post ID manquant.'}, status=400)

    try:
        post = ExtraBetPost.objects.get(id=post_id)
    except ExtraBetPost.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Publication introuvable.'}, status=404)

    if post.user_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'Vous ne pouvez supprimer que vos propres publications.'}, status=403)

    post.delete()
    return JsonResponse({'ok': True, 'message': 'Publication supprimee.'})


def _auto_seed_quiz_questions(subject: str, target: int = 40) -> int:
    """
    Génère des QCM via l'IA depuis les JSON d'examens structurés.
    Priorité : get_exam_context_json (rapide, varié) → fallback PDF brut.
    Retourne le nombre total de questions disponibles après seeding.
    """
    try:
        for attempt in range(5):
            if QuizQuestion.objects.filter(subject=subject).count() >= target:
                break
            # Utiliser les JSON pré-exportés en priorité (plus riche et plus rapide)
            exam_text = pdf_loader.get_exam_context_json(subject, max_chars=5000, variety_seed=attempt)
            if not exam_text:
                exam_text = pdf_loader.get_exam_context(subject, max_chars=5000, start_idx=attempt * 2)
            if not exam_text:
                break
            new_qs = gemini.extract_quiz_from_exam_text(exam_text, subject, count=12)
            for q in new_qs:
                enonce = str(q.get('enonce', '')).strip()
                if not enonce:
                    continue
                if not QuizQuestion.objects.filter(subject=subject, enonce=enonce).exists():
                    QuizQuestion.objects.create(
                        subject=subject,
                        enonce=enonce,
                        options=q.get('options', []),
                        reponse_correcte=str(q.get('reponse_correcte', 0)),
                        explication=q.get('explication', ''),
                        sujet=q.get('sujet', ''),
                    )
            import time as _time; _time.sleep(0.5)
    except Exception as e:
        import traceback; traceback.print_exc()
    return QuizQuestion.objects.filter(subject=subject).count()


def _background_seed(subject: str, target: int):
    """Lance le seeding dans un thread séparé pour ne pas bloquer la réponse."""
    import threading
    def _run():
        try:
            _auto_seed_quiz_questions(subject, target=target)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


import re as _re_filter

_CTX_DEPENDENT_RE = _re_filter.compile(
    r"(tableau ci-dessus|d'après le texte|selon l'énoncé|dans le document|"
    r"ci-contre|figure ci|données ci|d'après le graphe|d'après la figure|"
    r"d'après le tableau|dans l'extrait|d'après l'extrait|selon le graphique|"
    r"le texte dit|dans ce texte|d'après ce texte|selon ce texte|"
    r"d['']après le diagramme|d['']après le schéma|d['']après le document|"
    r"selon le tableau|selon la figure|selon le document|selon le diagramme|"
    r"d['']après l['']image|d['']après l['']illustration|dans le tableau|"
    r"dans la figure|dans le graphique|dans le graphe|dans le diagramme|"
    r"dans le schéma|d['']après le planning|d['']après l['']emploi du temps|"
    r"d['']après l['']horaire|d['']après le calendrier|d['']après le sondage|"
    r"d['']après l['']enquête|d['']après le rapport|selon l['']enquête|"
    r"le tableau (montre|indique|donne|présente|ci-dessus|ci-contre|suivant)|"
    r"la figure (montre|indique|donne|présente|ci-dessus|ci-contre|suivant)|"
    r"le graphique (montre|indique|donne|présente)|"
    r"tableau d['']occupation|tableau de (données|valeurs|fréquence|répartition|distribution)|"
    r"d['']après (ce |le |la |l[''])(tableau|figure|graphe|graphique|schéma|diagramme|image|document|texte|extrait|passage)|"
    # Questions referencing external data without explicit mention of table/figure
    r"quel(?:le)?\s+est\s+(?:le |la |les |l[''])?(?:pourcentage|proportion|nombre|effectif|fr[ée]quence)\s+(?:de|des|d[''])\s+\w+.*(?:entre|de|à|pendant)\s+\d|"
    r"combien\s+de\s+\w+.*(?:entre|de|à|pendant)\s+\d+\s*h|"
    r"lire\s+(?:le|la|les|un|une)|lire\s+graphiquement|lecture\s+graphique|"
    r"relever\s+(?:les|la|le)|(?:à\s+l['']aide|en\s+utilisant)\s+(?:du|de\s+la|des|de\s+l[''])\s*(?:tableau|graphe|graphique|figure|courbe|diagramme))",
    _re_filter.IGNORECASE
)


def _is_context_dependent(enonce: str) -> bool:
    """Return True if the question requires external data (table/figure/graph) to be answerable."""
    return bool(_CTX_DEPENDENT_RE.search(enonce))


_REPETITIVE_EN_QUESTION_RE = _re_filter.compile(
    r"(meaning\s+of\s+the\s+word\s*['\"]?resilient|"
    r"after\s+the\s+hurricane,?\s+the\s+community\s+remained\s+resilient)",
    _re_filter.IGNORECASE,
)


def _is_forbidden_repetitive_question(enonce: str, subject: str = '') -> bool:
    """Block known over-repeated prompts that degrade quiz/exam variety."""
    if not enonce:
        return False
    low_subj = (subject or '').lower()
    if low_subj == 'anglais' and _REPETITIVE_EN_QUESTION_RE.search(enonce):
        return True
    return False


def _local_filter_quiz_pool(items: list, subject: str, wanted: int = 40) -> list:
    """Fast local quality filter — no AI calls, runs in milliseconds."""
    import re as _re
    # Patterns that indicate VRAI/FAUX or multi-part exercises (not individual MCQs)
    bad_format = _re.compile(
        r"(vrai ou faux|true or false|indiquez vrai|indiquez.*faux|\b1[.)].*\n.*\b2[.])",
        _re.IGNORECASE | _re.DOTALL
    )
    approved = []
    for item in items:
        if len(approved) >= wanted:
            break
        enonce = str(item.get('enonce', '') or '')
        # Skip too short
        if len(enonce.strip()) < 15:
            continue
        # Skip context-dependent questions that require an external document
        if _is_context_dependent(enonce):
            continue
        if _is_forbidden_repetitive_question(enonce, subject):
            continue
        # Skip VRAI/FAUX and multi-part exercises
        if bad_format.search(enonce):
            continue
        # Skip if options are fewer than 4 (includes VRAI/FAUX 2-option items)
        opts = item.get('options', [])
        if item.get('type') == 'qcm' and len(opts) < 4:
            continue
        # Skip if all options are just VRAI/FAUX variants
        if opts and all(o.strip().upper() in ('VRAI', 'FAUX', 'TRUE', 'FALSE') for o in opts):
            continue
        # Skip purely administrative text (consigne de salle d'examen)
        if any(kw in enonce.lower() for kw in ['candidat', 'salle d\'examen', 'feuille de composition', 'nom et prénom du candidat']):
            continue
        approved.append(item)
    return approved


def get_quiz_questions_for_user(user, subject: str, count: int = 10, chapter: str = '', include_review: bool = True) -> dict:
    """Shared quiz source used by the quiz page and onboarding diagnostic."""
    from datetime import date as _date
    from .models import MistakeTracker

    REVIEW_INJECT = 3
    review_qs = []
    if include_review and user is not None:
        due_mistakes = list(
            MistakeTracker.objects.filter(
                user=user, subject=subject,
                mastered=False, next_review__lte=_date.today()
            ).order_by('next_review')[:REVIEW_INJECT]
        )
        review_qs = [{
            'enonce':           m.enonce,
            'options':          m.options,
            'reponse_correcte': m.reponse_correcte,
            'explication':      m.explication,
            'theme':            m.theme,
            'difficulte':       'difficile',
            'source':           'revision',
            'type':             'qcm',
            '_is_review':       True,
            '_mistake_id':      m.pk,
            '_wrong_count':     m.wrong_count,
        } for m in due_mistakes]

    new_count = max(3, count - len(review_qs))

    if subject == 'anglais':
        from pathlib import Path as _angPath
        import json as _angj, random as _angr
        _ang_file = _angPath(__file__).resolve().parent.parent / 'database' / 'quiz_anglais.json'
        try:
            _ang_raw = _angj.loads(_ang_file.read_text(encoding='utf-8'))
            _ang_qs = _ang_raw if isinstance(_ang_raw, list) else _ang_raw.get('quiz', [])
            if not _ang_qs:
                raise ValueError('empty')
            if chapter:
                _ang_f = [q for q in _ang_qs if chapter.lower() in q.get('category', '').lower()]
                if _ang_f:
                    _ang_qs = _ang_f
            _angr.shuffle(_ang_qs)
            _ang_conv = []
            for _q in _ang_qs:
                if len(_ang_conv) >= count:
                    break
                _eno = _q.get('question', _q.get('enonce', ''))
                if _is_context_dependent(_eno):
                    continue
                if _is_forbidden_repetitive_question(_eno, subject):
                    continue
                _opts = list(_q.get('options', []))
                _cidx = {'A':0,'B':1,'C':2,'D':3}.get(_q.get('correct','A').upper(), 0)
                _ans = _opts[_cidx] if _cidx < len(_opts) else ''
                _angr.shuffle(_opts)
                try:
                    _rc = _opts.index(_ans)
                except ValueError:
                    _rc = 0
                _ang_conv.append({
                    'enonce': _eno,
                    'options': _opts,
                    'reponse_correcte': _rc,
                    'explication': _q.get('explanation', _q.get('explication', '')),
                    'theme': _q.get('category', 'Anglais'),
                    'difficulte': _q.get('difficulty', 'moyen'),
                    'source': 'quiz_anglais_json',
                    'type': 'qcm',
                })
            if _ang_conv:
                return {'questions': review_qs + _ang_conv, 'source': 'json_anglais', 'review_count': len(review_qs)}
        except Exception:
            import traceback; traceback.print_exc()
        # Fallback to AI
        direct_qs = gemini.generate_quiz_questions(subject, count=new_count, chapter=chapter)
        if direct_qs:
            random.shuffle(direct_qs)
            return {'questions': review_qs + direct_qs, 'source': 'ai_anglais', 'review_count': len(review_qs)}
        return {'error': 'Génération IA échouée. Réessaie dans quelques secondes.', 'questions': []}

    if subject == 'espagnol':
        from pathlib import Path as _espPath
        import json as _espj, random as _espr
        _esp_file = _espPath(__file__).resolve().parent.parent / 'database' / 'quiz_espagnol.json'
        try:
            _esp_raw = _espj.loads(_esp_file.read_text(encoding='utf-8'))
            _esp_qs = _esp_raw if isinstance(_esp_raw, list) else _esp_raw.get('quiz', [])
            if not _esp_qs:
                raise ValueError('empty')
            if chapter:
                _esp_f = [q for q in _esp_qs if chapter.lower() in q.get('category', '').lower()]
                if _esp_f:
                    _esp_qs = _esp_f
            _espr.shuffle(_esp_qs)
            _esp_conv = []
            for _q in _esp_qs:
                if len(_esp_conv) >= count:
                    break
                _eno = _q.get('question', _q.get('enonce', ''))
                if _is_context_dependent(_eno):
                    continue
                if _is_forbidden_repetitive_question(_eno, subject):
                    continue
                _opts = list(_q.get('options', []))
                _cidx = {'A':0,'B':1,'C':2,'D':3}.get(_q.get('correct','A').upper(), 0)
                _ans = _opts[_cidx] if _cidx < len(_opts) else ''
                _espr.shuffle(_opts)
                try:
                    _rc = _opts.index(_ans)
                except ValueError:
                    _rc = 0
                _esp_conv.append({
                    'enonce': _eno,
                    'options': _opts,
                    'reponse_correcte': _rc,
                    'explication': _q.get('explanation', _q.get('explication', '')),
                    'theme': _q.get('category', 'Espagnol'),
                    'difficulte': _q.get('difficulty', 'moyen'),
                    'source': 'quiz_espagnol_json',
                    'type': 'qcm',
                })
            if _esp_conv:
                return {'questions': review_qs + _esp_conv, 'source': 'json_espagnol', 'review_count': len(review_qs)}
        except Exception:
            import traceback; traceback.print_exc()
        # Fallback to AI
        direct_qs = gemini.generate_quiz_questions(subject, count=new_count, chapter=chapter)
        if direct_qs:
            random.shuffle(direct_qs)
            return {'questions': review_qs + direct_qs, 'source': 'ai_espagnol', 'review_count': len(review_qs)}
        return {'error': 'Génération IA échouée. Réessaie dans quelques secondes.', 'questions': []}

    if subject == 'francais':
        from pathlib import Path as _krPath
        import json as _krj, random as _rnd
        _kr_file = _krPath(__file__).resolve().parent.parent / 'database' / 'quiz_kreyol.json'
        try:
            _kr_data = _krj.loads(_kr_file.read_text(encoding='utf-8'))
            _kr_qs = _kr_data.get('quiz', [])
            if not _kr_qs:
                return {'error': 'Quiz Kreyòl vide.', 'questions': []}
            _filtered = [q for q in _kr_qs if chapter.lower() in q.get('category', '').lower()] if chapter else _kr_qs
            if not _filtered:
                _filtered = _kr_qs
            _rnd.shuffle(_filtered)
            _converted = []
            for _q in _filtered:
                if len(_converted) >= count:
                    break
                _kr_enonce = _q.get('question', '')
                if _is_context_dependent(_kr_enonce):
                    continue
                _opts = list(_q.get('options', []))
                _correct_letter = _q.get('correct', 'A').upper()
                _correct_idx = {'A':0,'B':1,'C':2,'D':3}.get(_correct_letter, 0)
                _answer_text = _opts[_correct_idx] if _correct_idx < len(_opts) else ''
                _rnd.shuffle(_opts)
                try:
                    _rc = _opts.index(_answer_text)
                except ValueError:
                    _rc = 0
                _converted.append({
                    'enonce': _kr_enonce,
                    'options': _opts,
                    'reponse_correcte': _rc,
                    'explication': _q.get('explanation', _q.get('explication', '')),
                    'theme': _q.get('category', 'Kreyòl'),
                    'difficulte': _q.get('difficulty', 'moyen'),
                    'source': 'quiz_kreyol',
                    'type': 'qcm',
                })
            return {'questions': _converted, 'source': 'kreyol_manual', 'review_count': 0}
        except Exception:
            import traceback; traceback.print_exc()
            return {'error': 'Erreur chargement quiz Kreyòl.', 'questions': []}

    _JSON_QUIZ_FILES = {
        'svt': 'quiz_SVT.json',
        'histoire': 'quiz_sc_social.json',
        'physique': 'quiz_physique.json',
        'philosophie': 'quiz_philosophie.json',
        'informatique': 'quiz_informatique.json',
        'economie': 'quiz_economie.json',
        'chimie': 'quiz_chimie.json',
        'art': 'quiz_art.json',
        'maths': 'quiz_math.json',
    }
    if subject in _JSON_QUIZ_FILES:
        from pathlib import Path as _jPath
        import json as _jj, random as _jr
        _j_file = _jPath(__file__).resolve().parent.parent / 'database' / _JSON_QUIZ_FILES[subject]
        try:
            _j_data = _jj.loads(_j_file.read_text(encoding='utf-8'))
            # Support both flat array format and {"quiz": [...]} format
            _j_qs = _j_data if isinstance(_j_data, list) else _j_data.get('quiz', [])
            if not _j_qs:
                return {'error': f'Aucune question disponible pour {subject}.', 'questions': []}
            if chapter:
                _ch_lower = chapter.lower()
                # SVT: filtre par champ 'discipline' (biologie/geologie) en priorité
                if subject == 'svt' and _ch_lower in ('biologie', 'geologie'):
                    _jf = [q for q in _j_qs if q.get('discipline', '').lower() == _ch_lower]
                    if _jf:
                        _j_qs = _jf
                else:
                    _jf = [q for q in _j_qs if _ch_lower in q.get('category', '').lower()]
                    if _jf:
                        _j_qs = _jf
            _jr.shuffle(_j_qs)
            _jconv = []
            for _q in _j_qs:
                if len(_jconv) >= count:
                    break
                _enonce_text = _q.get('question', _q.get('enonce', ''))
                # Skip context-dependent questions (need table/figure/graph)
                if _is_context_dependent(_enonce_text):
                    continue
                _opts = list(_q.get('options', []))
                _correct_letter = _q.get('correct', 'A').upper()
                _correct_idx = {'A':0,'B':1,'C':2,'D':3}.get(_correct_letter, 0)
                _answer_text = _opts[_correct_idx] if _correct_idx < len(_opts) else ''
                _jr.shuffle(_opts)
                try:
                    _rc = _opts.index(_answer_text)
                except ValueError:
                    _rc = 0
                _jconv.append({
                    'enonce': _enonce_text,
                    'options': _opts,
                    'reponse_correcte': _rc,
                    'explication': _q.get('explanation', _q.get('explication', '')),
                    'theme': _q.get('category', subject),
                    'difficulte': _q.get('difficulty', _q.get('difficulte', 'moyen')),
                    'source': f'quiz_{subject}_json',
                    'type': 'qcm',
                })
            if _jconv:
                return {'questions': _jconv, 'source': f'json_{subject}', 'review_count': 0}
            return {'error': 'Aucune question disponible.', 'questions': []}
        except Exception:
            import traceback; traceback.print_exc()
            return {'error': f'Erreur chargement quiz {subject}.', 'questions': []}

    from django.core.cache import cache as _cache
    _CACHE_KEY = f'quiz_approved_pool_{subject}'
    _CACHE_TTL = 86400
    pool = pdf_loader.get_quiz_items_pool(subject, chapter=chapter, size=new_count * 4)
    questions = []

    if pool:
        seen_enonced = set()
        if user is not None:
            recent_sessions = (QuizSession.objects
                               .filter(user=user, subject=subject)
                               .order_by('-completed_at')[:5])
            for s in recent_sessions:
                for d in (s.details or []):
                    q_text = d.get('question', '').strip()
                    if q_text:
                        seen_enonced.add(q_text[:80])

        unseen = [i for i in pool if i.get('enonce', '')[:80] not in seen_enonced]
        working_pool = unseen if len(unseen) >= count else pool

        approved = _cache.get(_CACHE_KEY)
        if approved is None:
            approved = _local_filter_quiz_pool(working_pool, subject, wanted=min(count * 4, 40))
            if approved:
                _cache.set(_CACHE_KEY, approved, _CACHE_TTL)
        else:
            approved = [q for q in approved if q.get('enonce', '')[:80] not in seen_enonced] or approved

        if approved:
            for item in approved:
                itype = item.get('type', 'question')
                opts = item.get('options', [])
                if itype == 'qcm' and len(opts) >= 4 and not all(o.strip().upper() in ('VRAI', 'FAUX', 'TRUE', 'FALSE') for o in opts):
                    rc = item.get('reponse_correcte', 0)
                    try:
                        rc_idx = int(rc)
                    except (ValueError, TypeError):
                        rc_idx = 0
                    questions.append({
                        'enonce': item.get('enonce', ''),
                        'options': opts,
                        'reponse_correcte': rc_idx,
                        'explication': item.get('explication', ''),
                        'theme': item.get('theme', ''),
                        'difficulte': item.get('difficulte', 'moyen'),
                        'source': item.get('source', ''),
                        'type': 'qcm',
                        '_qc_fixed': item.get('_qc_fixed', False),
                    })
            if len(questions) >= max(3, new_count // 2):
                random.shuffle(questions)
                final_qs = review_qs + questions[:new_count]
                return {'questions': final_qs, 'source': 'json_items', 'total_pool': len(pool), 'review_count': len(review_qs)}

    if subject in ('histoire', 'economie') and pool:
        open_pool = [it for it in pool if it.get('type') == 'question' and not it.get('options')]
        if open_pool:
            enriched = gemini.enrich_open_questions_to_qcm(open_pool, subject, count=new_count * 2)
            if enriched:
                qcm_approved = [q for q in questions if q.get('type') == 'qcm' and len(q.get('options', [])) >= 4]
                combined = (qcm_approved + enriched)[:new_count]
                random.shuffle(combined)
                return {'questions': review_qs + combined, 'source': 'json_enriched', 'review_count': len(review_qs)}

    total_available = QuizQuestion.objects.filter(subject=subject).count()
    if total_available == 0:
        _background_seed(subject, target=40)
        exam_ctx = pdf_loader.get_exam_context_json(subject, max_chars=3500)
        direct_qs = gemini.generate_quiz_questions(subject, count=new_count, exam_context=exam_ctx)
        if direct_qs:
            random.shuffle(direct_qs)
            return {'questions': review_qs + direct_qs, 'source': 'ai_direct', 'review_count': len(review_qs)}
        return {'error': 'Questions indisponibles. Réessaie dans quelques secondes.', 'questions': []}

    if total_available < new_count:
        total_available = _auto_seed_quiz_questions(subject, target=40)
    if total_available == 0:
        return {'error': 'Génération des questions en cours… Recharge dans 15 secondes.', 'questions': []}
    if total_available < 80:
        _background_seed(subject, target=min(total_available + 20, 200))

    seen_enonced = set()
    if user is not None:
        recent_sessions = (QuizSession.objects.filter(user=user, subject=subject).order_by('-completed_at')[:5])
        for s in recent_sessions:
            for d in (s.details or []):
                q_text = d.get('question', '').strip()
                if q_text:
                    seen_enonced.add(q_text)

    unseen_qs = list(
        QuizQuestion.objects.filter(subject=subject)
        .exclude(enonce__in=seen_enonced)
        .order_by('?')[:new_count]
    )
    db_questions = unseen_qs if len(unseen_qs) >= 5 else list(
        QuizQuestion.objects.filter(subject=subject).order_by('?')[:new_count]
    )
    questions = [q.to_dict() for q in db_questions]
    # Filter out context-dependent questions from DB too
    questions = [q for q in questions if not _is_context_dependent(q.get('enonce', ''))]
    questions = [q for q in questions if not _is_forbidden_repetitive_question(q.get('enonce', ''), subject)]
    random.shuffle(questions)
    final_qs = review_qs + questions
    return {'questions': final_qs, 'source': 'db', 'total_available': total_available, 'review_count': len(review_qs)}


def quiz_questions_api(request):
    """Endpoint AJAX — retourne 10 questions QCM/questions directes depuis les JSON d'examens.
    Pipeline :
      1. Charge un pool de questions (qcm + question) depuis les items reconstruits
      2. Passe chaque question au contrôle qualité Groq (correction ou skip si invalide)
      3. Fallback : génération IA traditionnelle si pool JSON vide
    """
    if not request.user.is_authenticated:
        if _is_guest(request):
            subject = request.GET.get('subject', 'maths')
            guest_done = request.session.get('guest_quiz_done', {})
            if guest_done.get(subject, 0) >= 1:
                return JsonResponse({'error': 'guest_limit', 'signup_url': '/signup/'}, status=403)
            # Demo rule: count is consumed when quiz is launched, not when it is submitted.
            guest_done[subject] = guest_done.get(subject, 0) + 1
            request.session['guest_quiz_done'] = guest_done
            request.session.modified = True
            payload = get_quiz_questions_for_user(None, subject=subject, count=5, chapter='', include_review=False)
            return JsonResponse(payload, status=200)
        return JsonResponse({'error': 'login_required'}, status=401)

    # ── Premium gate: 2 quiz/jour gratuits ──
    from core.premium import can_use_quiz, increment_quiz, premium_required_json
    allowed, remaining = can_use_quiz(request.user)
    if not allowed:
        return JsonResponse(premium_required_json(), status=403)

    try:
        subject = request.GET.get('subject', 'maths')
        chapter = request.GET.get('chapter', '')
        count = int(request.GET.get('count', 10))
        payload = get_quiz_questions_for_user(request.user, subject=subject, count=count, chapter=chapter, include_review=True)
        increment_quiz(request.user)
        return JsonResponse(payload, status=200)
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.', 'questions': []}, status=500)


@require_POST
def quiz_save_api(request):
    data, err = _parse_json_body(request)
    if err:
        return err
    if not request.user.is_authenticated:
        if _is_guest(request):
            # Already counted at quiz launch in quiz_questions_api.
            return JsonResponse({'ok': True, 'guest': True, 'signup_url': '/signup/'})
        return JsonResponse({'error': 'login_required'}, status=401)
    from datetime import date
    from .models import MistakeTracker
    subject = data.get('subject', 'maths')
    details = data.get('details', [])
    score   = data.get('score', sum(1 for d in details if d.get('ok')))
    total   = data.get('total', len(details))

    session = QuizSession.objects.create(
        user=request.user, subject=subject,
        score=score, total=total, details=details
    )

    stats = _get_or_create_stats(request.user)
    stats.quiz_completes += 1
    stats.minutes_etude  += 10  # ~10 min par quiz
    stats.save(update_fields=['quiz_completes', 'minutes_etude'])

    # ── Répétition espacée SM-2 ─────────────────────────────────────────
    today = date.today()
    for d in details:
        enonce = (d.get('question') or '').strip()
        if not enonce:
            continue
        q_hash   = MistakeTracker.make_hash(enonce)
        is_ok    = bool(d.get('ok'))
        is_review = bool(d.get('_is_review'))
        mid      = d.get('_mistake_id')

        if not is_ok:
            # ── Mauvaise réponse → créer ou aggraver la fiche SM-2 ─────
            opts = d.get('options', [])
            rc   = d.get('reponse_correcte', 0)
            try:
                rc_idx = int(rc)
            except (ValueError, TypeError):
                rc_idx = 0
            mt, created = MistakeTracker.objects.get_or_create(
                user=request.user, question_hash=q_hash,
                defaults={
                    'subject':          subject,
                    'enonce':           enonce,
                    'options':          opts,
                    'reponse_correcte': rc_idx,
                    'explication':      d.get('explication', ''),
                    'theme':            d.get('theme', ''),
                    'next_review':      today,
                }
            )
            if not created:
                mt.subject          = subject
                mt.options          = opts
                mt.reponse_correcte = rc_idx
                mt.explication      = d.get('explication', '') or mt.explication
                mt.theme            = d.get('theme', '') or mt.theme
            mt.apply_sm2(correct=False)
            mt.save()

        elif is_review:
            # ── Bonne réponse sur une question de révision → avancer SM-2
            try:
                if mid:
                    mt = MistakeTracker.objects.get(pk=mid, user=request.user)
                else:
                    mt = MistakeTracker.objects.get(user=request.user, question_hash=q_hash)
                mt.apply_sm2(correct=True)
                mt.save()
            except MistakeTracker.DoesNotExist:
                pass

    # ── Suivi adaptatif — mise à jour de la maîtrise par matière ───────
    try:
        from .learning_tracker import update_subject_mastery, log_learning_event
        for d in details:
            enonce = (d.get('question') or '').strip()
            if not enonce:
                continue
            update_subject_mastery(
                user=request.user,
                subject=subject,
                is_correct=bool(d.get('ok')),
                question_text=enonce,
                answer_text=str(d.get('user_answer', '')),
                topic=str(d.get('theme', '') or d.get('sujet', '') or ''),
            )
        log_learning_event(
            user=request.user,
            event_type='quiz_completed',
            subject=subject,
            details={'score': score, 'total': total, 'session_id': session.pk},
            score_pct=session.get_percentage(),
        )
    except Exception as _lt_err:
        print(f"[LEARNING_TRACKER] quiz: {_lt_err}")

    return JsonResponse({
        'ok': True, 'score': score, 'total': total,
        'pct': session.get_percentage(), 'session_id': session.pk,
    })


# ─────────────────────────────────────────────
# EXERCICES
# ─────────────────────────────────────────────
def _auto_seed_chapters(subject: str) -> list:
    """
    Génère automatiquement les chapitres depuis les PDFs programme si la BDD est vide.
    Appelé à la volée — pas besoin de manage.py generate_chapters_from_pdfs.
    """
    try:
        from pathlib import Path
        from django.conf import settings
        import pdfplumber, json as _json, re as _re

        CHAPTER_PDF_MAP = {
            'maths':    {'': ['Math--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf']},
            'physique': {'': ['Physique--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf']},
            'chimie':   {'': ['Chimie--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf']},
            'svt': {
                'biologie': ['Biologie--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf'],
                'geologie': ['Geologie--Programme_detaille--4eme_annee_Nouveau_Secondaire.pdf'],
            },
            'histoire':    {'': ['Sciences_Sociales--Programme_Detaille--4eme_annee_Nouveau_Secondaire.pdf']},
            'anglais':     {'': ['Anglais--Programme-detaille--4e_annee_Nouveau_Secondaire.pdf']},
            'philosophie': {},
        }
        SUBSECTION_LABELS = {'biologie': 'Biologie', 'geologie': 'Géologie'}
        db_dir = Path(getattr(settings, 'COURSE_DB_PATH', '')) / 'chapter'
        subsection_map = CHAPTER_PDF_MAP.get(subject, {})
        created_total = 0

        for subsection, pdfs in subsection_map.items():
            sub_label = MATS.get(subject, {}).get('label', subject)
            if subsection:
                sub_label += f' — {SUBSECTION_LABELS.get(subsection, subsection)}'
            full_text = ''
            for pdf_name in pdfs:
                pdf_path = db_dir / pdf_name
                if not pdf_path.exists():
                    continue
                try:
                    with pdfplumber.open(str(pdf_path)) as pdf:
                        for page in pdf.pages:
                            t = page.extract_text()
                            if t:
                                full_text += t + '\n'
                except Exception:
                    pass
            if not full_text.strip():
                continue

            prompt = (
                f"Tu es expert du programme Bac Haïti en {sub_label}.\n"
                f"Programme officiel :\n{full_text[:5000]}\n\n"
                f"Génère la liste complète des chapitres à maîtriser pour le Bac.\n"
                "JSON array UNIQUEMENT :\n"
                '[{"title":"...","description":"...","order":1}]'
            )
            raw = gemini._call(prompt, max_tokens=2000)
            raw = _re.sub(r'```[a-z]*\s*', '', raw).strip()
            m = _re.search(r'\[[\s\S]+\]', raw)
            if not m:
                continue
            try:
                chapters = _json.loads(m.group(0))
            except Exception:
                continue
            for i, chap in enumerate(chapters if isinstance(chapters, list) else []):
                title = chap.get('title', '').strip()
                if not title:
                    continue
                SubjectChapter.objects.get_or_create(
                    subject=subject, subsection=subsection, title=title,
                    defaults={'description': chap.get('description', ''), 'order': chap.get('order', i + 1)}
                )
                created_total += 1
    except Exception:
        import traceback; traceback.print_exc()
    return list(SubjectChapter.objects.filter(subject=subject).order_by('subsection', 'order').values('id', 'title', 'subsection'))


def _build_exercices_chapters():
    """Build chapters_by_subject dict for the exercices page (shared by guest + auth)."""
    chapters_by_subject = {}
    _EXO_SUBJECTS_EXCLUDE = {'francais', 'histoire', 'informatique', 'art'}
    SUBJECT_JSON_MAP = {
        'maths':       'note_math.json',
        'chimie':      'note_de_Chimie.json',
        'svt':         'note_SVT.json',
        'francais':    'note_kreyol.json',
        'philosophie': 'note_philosophie.json',
        'anglais':     'note_anglais.json',
        'espagnol':    'note_espagnol.json',
        'economie':    'note_economie.json',
        'informatique':'note_informatique.json',
        'art':         'note_art.json',
    }
    from pathlib import Path as _Path
    import re as _re2
    _db_dir = _Path(__file__).resolve().parent.parent / 'database'

    # Histoire
    _histoire_note_path = _db_dir / 'note_histoire.json'
    try:
        import json as _jh
        _hist_raw = _jh.loads(_histoire_note_path.read_text(encoding='utf-8'))
        _hist_text = _hist_raw.get('raw_text', '')
        _hist_chapters = []
        for _line in _hist_text.splitlines():
            _m = _re2.match(r'^CHAPITRE\s+\d+\s*[—\-:]\s*(.+)$', _line.strip(), _re2.IGNORECASE)
            if _m:
                _hist_chapters.append(_m.group(0).strip())
        if _hist_chapters:
            chapters_by_subject['histoire'] = [
                {'id': i+1, 'title': t, 'num': i+1}
                for i, t in enumerate(_hist_chapters)
            ]
    except Exception:
        pass

    # Physique
    _PHYS_DISPLAY_MAP = {
        'Chapitre « Démonstrations » (uniquement des exercices réels)': 'Démonstration',
        'Démonstrations': 'Démonstration',
        'Démonstrations (Exercices BAC réels)': 'Démonstration',
        'Chapitre 1 – Courant alternatif': 'Courant alternatif',
        'Chapitre 1 - Courant alternatif': 'Courant alternatif',
        'Chapitre 2 – Chute libre': 'Chute libre',
        'Chapitre 2 - Chute libre': 'Chute libre',
        'Chapitre 3 – Projectile': 'Projectile',
        'Chapitre 3 - Projectile': 'Projectile',
        'Chapitre 4 – Magnétisme': 'Magnétisme',
        'Chapitre 4 - Magnétisme': 'Magnétisme',
        'Chapitre 5 – Condensateur': 'Condensateur',
        'Chapitre 5 - Condensateur': 'Condensateur',
        'Chapitre 6 – Induction électromagnétique': 'Induction électromagnétique',
        'Chapitre 6 - Induction électromagnétique': 'Induction électromagnétique',
        'Chapitre 7 – Pendule': 'Pendule',
        'Chapitre 7 - Pendule': 'Pendule',
    }
    try:
        from . import exo_loader as _exo_loader_phys
        _phys_chaps = _exo_loader_phys.get_chapters('physique')
        if _phys_chaps:
            for _ch in _phys_chaps:
                _ch['display'] = _PHYS_DISPLAY_MAP.get(_ch['title'], _ch['title'])
            chapters_by_subject['physique'] = _phys_chaps
    except Exception:
        pass

    for subj in MATS:
        if subj in ('physique', 'histoire'):
            continue
        if subj in _EXO_SUBJECTS_EXCLUDE:
            continue
        if subj == 'philosophie':
            chapters_by_subject['philosophie'] = [
                {'id': 1, 'title': 'Dissertation', 'num': 1},
                {'id': 2, 'title': 'Étude de texte', 'num': 2},
            ]
            continue
        if subj in ('maths', 'chimie'):
            try:
                from . import exo_loader as _exo_loader_subj
                _subj_chaps = _exo_loader_subj.get_chapters(subj)
                if _subj_chaps:
                    if subj == 'chimie':
                        # Ajouter le chapitre des équations chimiques si le fichier existe
                        _eq_chim_path = _db_dir / 'equation_chimique.json'
                        if _eq_chim_path.exists():
                            _next_id = max((c['id'] for c in _subj_chaps), default=0) + 1
                            _subj_chaps.append({
                                'id': _next_id,
                                'title': 'Écrire les équations chimiques',
                                'num': _next_id,
                            })
                    chapters_by_subject[subj] = _subj_chaps
                    continue
            except Exception:
                pass
        if subj == 'svt':
            chapters_by_subject['svt'] = [
                {'id': 1, 'title': 'Génétique – Croisements (monohybridisme, dihybridisme)', 'num': 1},
                {'id': 2, 'title': 'Hérédité liée au sexe (daltonisme, hémophilie, myopathie)', 'num': 2},
                {'id': 3, 'title': 'Génétique moléculaire (mutations, drépanocytose)', 'num': 3},
                {'id': 4, 'title': 'Caryotype et anomalies chromosomiques', 'num': 4},
                {'id': 5, 'title': 'Transgénèse et code génétique', 'num': 5},
            ]
            continue
        fn = SUBJECT_JSON_MAP.get(subj)
        if fn:
            _fp = _db_dir / fn
            try:
                import json as _j2
                _raw = _j2.loads(_fp.read_text(encoding='utf-8'))
                _chs = _raw.get('chapitres', _raw.get('chapters', []))
                if _chs:
                    chapters_by_subject[subj] = [
                        {'id': i+1, 'title': c.get('titre', c.get('title', '')), 'num': i+1}
                        for i, c in enumerate(_chs)
                        if c.get('titre', c.get('title',''))
                    ]
                    continue
            except Exception:
                pass
        chaps = pdf_loader.get_chapters_from_json(subj)
        if chaps:
            chapters_by_subject[subj] = [
                {'id': c.get('num', i+1), 'title': c.get('title', ''), 'num': c.get('num', i+1)}
                for i, c in enumerate(chaps)
            ]
        else:
            db_chaps = list(SubjectChapter.objects.filter(subject=subj).order_by('subsection', 'order'))
            if db_chaps:
                chapters_by_subject[subj] = [{'id': c.pk, 'title': c.title, 'num': c.pk} for c in db_chaps]
            else:
                chapters_by_subject[subj] = []
    return chapters_by_subject


def exercices_view(request):
    """Page exercices — chapitres depuis JSON, exercices depuis vrais examens BAC."""
    if not request.user.is_authenticated:
        if _is_guest(request):
            # Build real chapters for guests so they can browse
            guest_chapters = _build_exercices_chapters()
            _EXO_EXCLUDE_ALWAYS = {'francais', 'histoire', 'informatique', 'art'}
            demo_subjs = _GUEST_DEMO.get('user_serie_subjects', list(MATS.keys()))
            guest_mats = {k: v for k, v in MATS.items() if k in demo_subjs and k not in _EXO_EXCLUDE_ALWAYS}
            return render(request, 'core/exercices.html', {
                'is_guest': True, 'mats': guest_mats,
                'chapters_by_subject': json.dumps(guest_chapters),
                'profile': None,
            })
        return redirect('/login/?next=' + request.get_full_path())
    chapters_by_subject = _build_exercices_chapters()
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Matières pour la page exercices (filtrées par série, sans exo disponible)
    _EXO_EXCLUDE_ALWAYS = {'francais', 'histoire', 'informatique', 'art'}
    user_subjs = _get_user_serie_subjects(request.user)
    exo_mats = {k: v for k, v in MATS.items() if k in user_subjs and k not in _EXO_EXCLUDE_ALWAYS}

    return render(request, 'core/exercices.html', {
        'mats': exo_mats,
        'chapters_by_subject': json.dumps(chapters_by_subject),
        'profile': profile,
    })


@login_required
@require_POST
def solve_api(request):
    try:
        text  = request.POST.get('text', '').strip()
        image = request.FILES.get('image')
        image_data = image.read() if image else None
        image_mime = image.content_type if image else None

        result = gemini.solve_exercise(text, image_data, image_mime)

        stats = _get_or_create_stats(request.user)
        stats.exercices_resolus += 1
        stats.minutes_etude += 5   # ~5 min par exercice
        stats.save(update_fields=['exercices_resolus', 'minutes_etude'])

        return JsonResponse({'solution': result})
    except Exception as e:
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


def api_get_exercise(request):
    """Retourne un exercice de style BAC propre et complet.
    Pipeline :
      1. **NOUVEAU** Vrais exercices du BAC depuis BACExercise table (100% original)
      2. Pool d'exercices depuis les items reconstruits (rebuild_exam_json)
      3. Contrôle qualité Groq — corrige ou skip si exercice invalide/incomplet
      4. Fallback IA live sur texte brut si pool vide
      5. Fallback JSON structuré parsé
      6. Fallback final IA pure
    """
    if not request.user.is_authenticated:
        if _is_guest(request):
            guest_exo_done = request.session.get('guest_exo_done', 0)
            if guest_exo_done >= 2:
                return JsonResponse({'error': 'guest_limit', 'message': 'Tu as atteint la limite de 2 exercices en mode démo. Crée un compte pour continuer !', 'signup_url': '/signup/', 'premium_required': True}, status=403)
            # Demo rule: exercise is consumed when launched.
            request.session['guest_exo_done'] = guest_exo_done + 1
            request.session.modified = True
        else:
            return JsonResponse({'error': 'login_required'}, status=401)

    # ── Premium gate: 1 exercice/matière/mois gratuit (skip for guests) ──
    if request.user.is_authenticated:
        from core.premium import can_use_exercise, increment_exercise, premium_required_json
        subject_check = request.GET.get('subject', 'maths')
        allowed, remaining = can_use_exercise(request.user, subject_check)
        if not allowed:
            return JsonResponse(premium_required_json(), status=403)

    try:
        subject = request.GET.get('subject', 'maths')
        chapter = request.GET.get('chapter', '')

        exercise_data = None

        # ── Chapitres : mots-clés tirés des vrais examens BAC Haïti ─────────
        # Source : database/json/exams_physique.json — thèmes et intros réels
        _CHAPTER_RULES = {
            # ── Maths ───────────────────────────────────────────────────────
            'probabilités': {
                'must_include': ['probabilité', 'probabilit', 'aléa', 'alea', 'dénombrement', 'dénombr', 'bernoulli', 'variable aléatoire', 'événement'],
                'must_exclude': ['régression', 'regression', 'corrélation', 'correlation', 'nuage de points', 'ajustement', 'série statistique', 'covariance', 'médiane', 'histogramme'],
            },
            'statistiques': {
                'must_include': ['statistique', 'régression', 'regression', 'corrélation', 'correlation', 'nuage', 'ajustement', 'covariance', 'série', 'moyenne', 'variance', 'écart-type'],
                'must_exclude': ['probabilité', 'probabilit', 'bernoulli', 'dénombrement', 'événement', 'espace probabilisé'],
            },
            # ── Physique — vocabulaire réel des examens BAC ──────────────
            # CH1 : condensateur DC (charge/décharge, plaques, diélectrique, montage)
            'chapitre 1 : le condensateur': {
                'must_include': [
                    'condensateur', 'capacité', 'capacitance', 'farad',
                    'plaques', 'armature', 'diélectrique', 'charge q',
                    'décharge', 'circuit rc', 'partage de charge',
                    'énergie électrique', 'permittivité',
                ],
                'must_exclude': [
                    'sinusoïdal', 'alternatif', 'rlc',
                    'solénoïde', 'bobine', 'inductance',
                    'induction électromagnétique', 'fem induite', 'courant induit',
                    'force de laplace', 'galvanomètre', 'rail',
                    'projectile', 'balistique', 'fléchette',
                    'pendule', 'oscillateur mécanique',
                    'onde mécanique', 'diffraction',
                    'rectiligne uniformément', 'mrua',
                ],
            },
            # CH2 : solénoïde et inductance (spires, champ B propre, perméabilité)
            'chapitre 2 : le solénoïde et l\'inductance': {
                'must_include': [
                    'solénoïde', 'bobine', 'inductance', 'spire',
                    'champ magnétique', 'perméabilité', 'auto-induction',
                    'flux magnétique', 'noyau',
                ],
                'must_exclude': [
                    'condensateur', 'capacité', 'sinusoïdal', 'rlc',
                    'induction électromagnétique', 'courant induit', 'fem induite',
                    'force de laplace', 'galvanomètre', 'rail',
                    'projectile', 'balistique', 'fléchette',
                    'pendule simple', 'oscillateur mécanique',
                    'onde mécanique', 'diffraction',
                    'rectiligne uniformément', 'mrua',
                ],
            },
            # CH3 : induction électromagnétique (faraday, lenz, FEM induite, courant induit)
            'chapitre 3 : induction électromagnétique': {
                'must_include': [
                    'induction électromagnétique', 'fem induite', 'courant induit',
                    'variation de flux', 'faraday', 'lenz',
                    'force électromotrice induite',
                ],
                'must_exclude': [
                    'condensateur', 'capacité', 'sinusoïdal', 'rlc',
                    'force de laplace', 'galvanomètre', 'rail',
                    'projectile', 'balistique',
                    'pendule', 'oscillateur mécanique',
                    'onde mécanique', 'diffraction',
                    'rectiligne uniformément', 'mrua',
                ],
            },
            # CH4 : force de Laplace et galvanomètre
            'chapitre 4 : force de laplace et galvanomètre': {
                'must_include': [
                    'force de laplace', 'galvanomètre', 'conducteur rectiligne',
                    'tiges parallèles', 'rail', 'aiguille aimantée',
                    'moment magnétique', 'force magnétique sur',
                ],
                'must_exclude': [
                    'condensateur', 'capacité', 'sinusoïdal', 'rlc',
                    'induction électromagnétique', 'courant induit', 'fem induite',
                    'projectile', 'balistique',
                    'pendule', 'oscillateur mécanique',
                    'onde mécanique', 'diffraction',
                    'rectiligne uniformément', 'mrua',
                ],
            },
            # CH5 : courant alternatif sinusoïdal (RLC, impédance, pulsation, valeur efficace)
            'chapitre 5 : courant alternatif sinusoïdal': {
                'must_include': [
                    'alternatif', 'sinusoïdal', 'impédance',
                    'pulsation', 'efficace', 'résonance', 'déphasage',
                    'régime sinusoïdal', 'valeur efficace', 'intensité efficace',
                    'réactance',
                ],
                'must_exclude': [
                    'projectile', 'balistique', 'fléchette',
                    'pendule simple', 'oscillateur mécanique',
                    'onde mécanique', 'diffraction', 'interférence optique',
                    'double fente',
                    'rectiligne uniformément', 'mrua',
                ],
            },
            # CH6 : cinématique rectiligne (MRUA, MRU, équation horaire, accélération constante)
            'chapitre 6 : cinématique — mouvement rectiligne': {
                'must_include': [
                    'mouvement rectiligne', 'mrua', 'mru', 'mruv',
                    'équation horaire', 'accélération constante',
                    'cinématique', 'rectiligne uniformément',
                ],
                'must_exclude': [
                    'condensateur', 'solénoïde', 'bobine', 'inductance',
                    'courant alternatif', 'sinusoïdal', 'rlc', 'impédance',
                    'induction électromagnétique', 'force de laplace', 'galvanomètre',
                    'projectile', 'balistique', 'trajectoire parabolique',
                    'pendule', 'oscillateur mécanique', 'ressort masse',
                    'onde mécanique', 'diffraction',
                ],
            },
            # CH7 : mouvement de projectile / balistique
            'chapitre 7 : mouvement de projectile (balistique)': {
                'must_include': [
                    'projectile', 'balistique', 'trajectoire', 'portée',
                    'angle de tir', 'lancé horizontalement', 'fléchette',
                ],
                'must_exclude': [
                    'condensateur', 'solénoïde', 'bobine', 'inductance',
                    'courant alternatif', 'sinusoïdal', 'rlc', 'impédance',
                    'induction électromagnétique', 'force de laplace', 'galvanomètre',
                    'pendule simple', 'oscillateur mécanique', 'ressort masse',
                    'onde mécanique', 'diffraction',
                    'rectiligne uniformément', 'mrua',
                ],
            },
            # CH8 : pendule simple et oscillations (ressort-masse, x(t)=A cos, période propre)
            'chapitre 8 : pendule simple et oscillations': {
                'must_include': [
                    'pendule', 'oscillat', 'ressort',
                    'période propre', 'pulsation propre',
                    'oscillateur mécanique', 'amplitude', 'fréquence propre',
                ],
                'must_exclude': [
                    'condensateur', 'solénoïde', 'bobine', 'inductance',
                    'courant alternatif', 'sinusoïdal', 'rlc', 'impédance',
                    'induction électromagnétique', 'force de laplace', 'galvanomètre',
                    'projectile', 'balistique', 'fléchette',
                    'onde mécanique', 'diffraction', 'double fente',
                    'rectiligne uniformément', 'mrua',
                ],
            },
            # CH9 : ondes (longueur d'onde, diffraction, interférences, réfraction, célérité)
            'chapitre 9 : ondes': {
                'must_include': [
                    'onde', 'longueur d\'onde', 'célérité',
                    'diffraction', 'interférence', 'réfraction', 'double fente',
                    'vibration mécanique', 'propagation d\'onde',
                    'onde électromagnétique', 'indice de réfraction',
                ],
                'must_exclude': [
                    'condensateur', 'solénoïde', 'bobine', 'inductance',
                    'courant alternatif', 'sinusoïdal', 'rlc', 'impédance',
                    'induction électromagnétique', 'force de laplace', 'galvanomètre',
                    'projectile', 'balistique',
                    'pendule', 'oscillateur mécanique',
                    'rectiligne uniformément', 'mrua',
                ],
            },
        }
        _chapter_rule = _CHAPTER_RULES.get(chapter.lower().strip()) if chapter else None

        def _respects_chapter_rule(exo_data):
            """Vérifie que l'exercice correspond bien au chapitre demandé."""
            if not _chapter_rule:
                return True
            text_to_check = ' '.join([
                str(exo_data.get('intro', '')),
                str(exo_data.get('enonce', '')),
                str(exo_data.get('theme', '')),
                ' '.join(str(q) for q in exo_data.get('questions', [])),
            ]).lower()
            # Must contain at least one must_include keyword
            has_required = any(kw in text_to_check for kw in _chapter_rule['must_include'])
            # Must not contain excluded keywords
            has_excluded = any(kw in text_to_check for kw in _chapter_rule['must_exclude'])
            return has_required and not has_excluded

        # ── 0a. PRIORITÉ : PHILOSOPHIE — exercice spécialisé (dissertation ou étude de texte) ──
        if subject == 'philosophie':
            exercise_data = gemini.generate_philosophy_exercise(chapter or 'Étude de texte')
            if exercise_data:
                exercise_data['_is_real_bac'] = False
            return JsonResponse({'ok': True, 'exercise': exercise_data}) if exercise_data else JsonResponse({'error': 'Aucun exercice disponible.'}, status=404)

        # ── 0b. Équations chimiques depuis equation_chimique.json ─────────────
        _EQ_CHIM_KEYWORDS = ('écrire les équations', 'equation chimique', 'équation chimique',
                              'ecrire les equations', 'équations chimiques')
        if not exercise_data and subject == 'chimie' and chapter and any(kw in chapter.lower() for kw in _EQ_CHIM_KEYWORDS):
            try:
                import json as _eqjson, random as _eqrand
                from pathlib import Path as _EqPath
                _eq_file = _EqPath(__file__).resolve().parent.parent / 'database' / 'equation_chimique.json'
                if _eq_file.exists():
                    _eq_data = _eqjson.loads(_eq_file.read_text(encoding='utf-8'))
                    _eq_exos = _eq_data.get('exercices', [])
                    if _eq_exos:
                        _eq_pick = _eqrand.choice(_eq_exos)
                        _rep = _eq_pick.get('reponse', {})
                        _sol_parts = []
                        if 'equation_equilibree' in _rep:
                            _sol_parts.append(f"**Équation équilibrée :** {_rep['equation_equilibree']}")
                        if 'masses_molaires' in _rep:
                            _mm_str = ', '.join(f"{k} = {v}" for k, v in _rep['masses_molaires'].items())
                            _sol_parts.append(f"**Masses molaires :** {_mm_str}")
                        if 'formule_brute' in _rep:
                            _sol_parts.append(f"**Formule brute :** {_rep['formule_brute']}")
                        if 'masse_molaire' in _rep:
                            _sol_parts.append(f"**Masse molaire :** {_rep['masse_molaire']}")
                        exercise_data = {
                            'intro':      _eq_pick['enonce'],
                            'enonce':     _eq_pick['enonce'],
                            'questions':  [],
                            'theme':      'Écrire les équations chimiques',
                            'matiere':    'CHIMIE',
                            'difficulte': 'moyen',
                            'source':     'Chimie BAC',
                            'solution':   '\n'.join(_sol_parts),
                            'conseils':   '',
                            '_is_real_bac': False,
                        }
            except Exception as _eq_err:
                print(f'[api_get_exercise] equation_chimique error: {_eq_err}')

        # ── 0. PRIORITÉ IA POUR MATIÈRES DE LANGUE ──────────────────────────
        # Pour anglais, espagnol, kreyol: génération 100% IA style BAC
        # Quantité illimitée, textes variés, toujours dans la langue cible
        LANGUAGE_SUBJECTS = {'anglais', 'espagnol', 'kreyol'}
        if subject in LANGUAGE_SUBJECTS:
            exercise_data = gemini.generate_language_exercise(subject, chapter)
            if exercise_data and not exercise_data.get('questions'):
                exercise_data = None  # retry via fallback if empty

        # ── 1. Vrais exercices depuis exo*.json (priorité absolue) ───────────
        # SVT, physique, maths, chimie → on lit les vrais exos sans IA
        if not exercise_data and subject not in LANGUAGE_SUBJECTS and subject != 'philosophie':
            try:
                from . import exo_loader as _exo_loader
                _exo = _exo_loader.get_random_exercise(subject, chapter)
                if _exo:
                    _questions = _exo.get('questions') or []
                    # Si l'énoncé est trop court (fill-in-the-blank chimie tail), ignorer
                    _intro = (_exo.get('intro') or _exo.get('enonce') or '').strip()
                    if len(_intro) >= 30:
                        exercise_data = {
                            'intro':      _intro,
                            'enonce':     _exo.get('enonce') or _intro,
                            'questions':  _questions,
                            'theme':      (_exo.get('theme') or _exo.get('chapter') or subject.upper()).strip(),
                            'matiere':    subject.upper(),
                            'difficulte': 'moyen',
                            'source':     _exo.get('source', ''),
                            'solution':   '',
                            'conseils':   '',
                            '_is_real_bac': True,
                        }
                        # ── Groq formatting review: fix tables, LaTeX artefacts ──
                        # Skip if intro already has a well-formed pipe table (avoid overwriting)
                        _has_table = '\n|' in exercise_data['intro'] or exercise_data['intro'].lstrip().startswith('|')
                        if not _has_table:
                            try:
                                _fmt = gemini.format_exercise_display(
                                    subject,
                                    exercise_data['intro'],
                                    exercise_data['questions'],
                                )
                                exercise_data['intro']     = _fmt['intro']
                                exercise_data['enonce']    = _fmt['intro']
                                exercise_data['questions'] = _fmt['questions']
                            except Exception as _fmt_err:
                                print(f'[api_get_exercise] format_exercise_display error: {_fmt_err}')
            except Exception as _exo_err:
                print(f'[api_get_exercise] exo_loader error: {_exo_err}')

        # ── 1b. (ancien) Vrais exercices du BAC depuis BACExercise ────────────
        # Conservé en fallback pour physique uniquement
        if not exercise_data and subject == 'physique':
            try:
                from .models import BACExercise, SubjectChapter
                
                # Trouver le chapitre physique correspondant
                chapter_obj = None
                if chapter:
                    chapter_obj = SubjectChapter.objects.filter(
                        subject='physique',
                        title__icontains=chapter
                    ).first()
                
                # Query les vrais exercices BAC
                if chapter_obj:
                    bac_exos = BACExercise.objects.filter(chapter=chapter_obj).order_by('?')[:1]
                else:
                    # Random across all chapters if no chapter specified
                    bac_exos = BACExercise.objects.filter(chapter__subject='physique').order_by('?')[:1]
                
                if bac_exos:
                    bac_ex = bac_exos[0]
                    content = bac_ex.content or ''
                    
                    # Parse the BAC exercise content into Q&A format
                    lines = content.split('\n')
                    questions = [line.strip() for line in lines if line.strip() and len(line) > 20][:3]
                    
                    exercise_data = {
                        'intro': f"Examen du Bac {bac_ex.exam_year} ({', '.join(bac_ex.exam_series)})",
                        'enonce': content[:1000],  # Limiter à 1000 chars
                        'questions': questions if questions else [content[:500]],
                        'theme': bac_ex.theme or 'Physique',
                        'matiere': 'PHYSIQUE',
                        'difficulte': 'moyen',
                        'source': f"Bac Haïti {bac_ex.exam_year}",
                        'solution': '',
                        'conseils': f"Exercice autentique du Bac Haïti {bac_ex.exam_year}. Vrais exercices historiques.",
                        '_is_real_bac': True,
                        '_bac_exercise_id': bac_ex.id,
                    }
            except ImportError:
                pass  # BACExercise pas importé, continue avec fallbacks
            except Exception as e:
                print(f"[DEBUG] Erreur BACExercise: {e}")

        # ── 2. Pool exercices depuis items reconstruits — TOUS sujets ─────────
        if not exercise_data:
            # Pour probabilités/statistiques, récupère un plus grand pool pour filtrer
            pool_size = 30 if _chapter_rule else 15
            pool = pdf_loader.get_exercise_items_pool(subject, chapter=chapter, size=pool_size)
            if pool:
                # Filtrer d'abord les exercices respectant le chapitre demandé
                if _chapter_rule:
                    def _pool_item_ok(it):
                        text_check = ' '.join([
                            str(it.get('intro', '')),
                            str(it.get('theme', '')),
                            ' '.join(str(q) for q in it.get('questions', [])),
                        ]).lower()
                        has_req = any(kw in text_check for kw in _chapter_rule['must_include'])
                        has_exc = any(kw in text_check for kw in _chapter_rule['must_exclude'])
                        return has_req and not has_exc
                    filtered_pool = [it for it in pool if _pool_item_ok(it)]
                    # Si aucun exercice valide dans le pool filtré, vider le pool
                    pool = filtered_pool if filtered_pool else []

                if pool:
                    # Contrôle qualité : prend le 1er exercice valide
                    approved = gemini.quality_check_pool(pool, subject, wanted=1)
                    if approved:
                        item = approved[0]
                        year = item.get('_year', '?')
                        intro     = item.get('intro', '').strip()
                        questions = [str(q).strip() for q in item.get('questions', []) if str(q).strip()]
                        candidate = {
                            'intro':      intro,
                            'enonce':     intro + '\n\n' + '\n'.join(questions),
                            'questions':  questions,
                            'theme':      item.get('theme', subject.upper()).strip(),
                            'matiere':    subject.upper(),
                            'difficulte': item.get('difficulte', 'moyen'),
                            'source':     f'Bac Haïti {year}',
                            'solution':   '',
                            'conseils':   f"Exercice extrait d'un vrai examen du Bac Haïti {year} en {subject.upper()}.",
                            '_qc_fixed':  item.get('_qc_fixed', False),
                            '_is_real_bac': False,
                        }
                        if _respects_chapter_rule(candidate):
                            exercise_data = candidate

        # ── 3. Fallback: IA live sur texte brut exam ─────────────────────────
        AI_SUBJECTS = {'maths', 'physique', 'chimie', 'svt'}
        if not exercise_data and subject in AI_SUBJECTS:
            raw_texts = pdf_loader.get_raw_exam_texts_for_ai(subject, chapter, max_chars=10000)
            if raw_texts:
                candidate = gemini.extract_structured_exercise(raw_texts, subject, chapter)
                if candidate and _respects_chapter_rule(candidate):
                    candidate['_is_real_bac'] = False
                    exercise_data = candidate

        # ── 4. Fallback : JSON structuré parsé ──────────────────────────────
        if not exercise_data:
            candidate = pdf_loader.get_exercise_from_json(subject, chapter)
            if candidate and _respects_chapter_rule(candidate):
                candidate['_is_real_bac'] = False
                exercise_data = candidate

        # ── 5. Fallback final : IA pure ──────────────────────────────────────
        if not exercise_data:
            exam_text = pdf_loader.get_exam_context(subject, max_chars=3000)
            exercise_data = gemini.generate_exam_exercise(subject, chapter, exam_text or '', chapter_rule=_chapter_rule)
            if exercise_data:
                exercise_data['_is_real_bac'] = False

        if not exercise_data:
            return JsonResponse({'error': 'Aucun exercice disponible pour cette matière.'}, status=404)

        # ── Post-traitement : nettoyer source + générer questions si manquantes ──
        import re as _re

        # 1. Nettoyer le nom de la source (supprimer .pdf, préfixes techniques)
        src = exercise_data.get('source', '')
        src = _re.sub(r'\.pdf$', '', src, flags=_re.IGNORECASE)
        src = _re.sub(r'\s*—\s*exam_[a-z_]+', '', src, flags=_re.IGNORECASE)
        src = _re.sub(r'exam_[a-z]+_[a-z]+-(\d{4})', r'Bac Haïti \1', src, flags=_re.IGNORECASE)
        exercise_data['source'] = src.strip()

        # 2. Si pas de questions, les générer depuis l'intro via l'IA
        questions = [str(q).strip() for q in (exercise_data.get('questions') or []) if str(q).strip()]
        if len(questions) < 2:
            intro = exercise_data.get('intro') or exercise_data.get('enonce', '')
            if intro and len(intro.strip()) > 30:
                gen_prompt = (
                    f"Voici l'énoncé d'un exercice de {subject} du Bac Haïti :\n\n{intro[:1500]}\n\n"
                    f"Génère 3 à 5 questions numérotées a), b), c)... précises et directes que l'élève "
                    f"doit résoudre. Réponds UNIQUEMENT avec la liste JSON : "
                    f'["a) question 1", "b) question 2", "c) question 3"]'
                )
                try:
                    q_raw = gemini._call_json(gen_prompt, max_tokens=500)
                    q_raw = _re.sub(r'```[a-z]*\s*', '', q_raw).strip()
                    m = _re.search(r'\[[\s\S]+\]', q_raw)
                    if m:
                        import json as _json2
                        gen_qs = _json2.loads(m.group(0))
                        gen_qs = [str(q).strip() for q in gen_qs if str(q).strip()]
                        if gen_qs:
                            exercise_data['questions'] = gen_qs
                except Exception:
                    pass

        if not _is_guest(request):
            increment_exercise(request.user, subject)
        return JsonResponse({'ok': True, 'exercise': exercise_data})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_analyze_exercise(request):
    """Analyze an exercise to determine which interactive tools to show (Punnett, avancement, table).
    POST: {subject, intro, enonce, questions[]}
    Returns: {ok, punnett, avancement, table, note_advice}
    """
    try:
        body = json.loads(request.body)
        subject   = body.get('subject', '')
        intro     = body.get('intro', '') or body.get('enonce', '') or ''
        enonce    = body.get('enonce', '') or ''
        questions = body.get('questions', [])
        # Load relevant note context (same mechanism as chat AI)
        user_message = f"{intro} {enonce} {' '.join(str(q) for q in questions)}"
        note_context = _get_db_context(subject, user_message)
        result = gemini.analyze_exercise_for_interactive(subject, intro, enonce, questions, note_context=note_context)
        return JsonResponse({'ok': True, **result})
    except Exception as e:
        return JsonResponse({'ok': False, 'punnett': None, 'avancement': None, 'table': None, 'note_advice': ''})


@login_required
def api_correct_exercise(request):
    """Évalue les réponses ouvertes d'un étudiant pour un exercice.
    POST: {exercise: {...}, answers: [...], subject: str}
    Returns: {corrections: [...], global_score, max_score, global_feedback}
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'login_required'}, status=401)
    try:
        import json as _json
        body = _json.loads(request.body)
        exercise = body.get('exercise', {})
        answers  = body.get('answers', [])
        subject  = body.get('subject', 'maths')
        question_index = body.get('question_index', None)
        force_lang = body.get('force_lang', '')
        user_lang = force_lang if force_lang in {'fr', 'kr'} else _get_user_lang(request)

        if question_index is not None:
            try:
                question_index = int(question_index)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'question_index invalide'}, status=400)
            questions = exercise.get('questions', []) or []
            if question_index < 0 or question_index >= len(questions):
                return JsonResponse({'error': 'question_index hors limite'}, status=400)
            exercise = dict(exercise)
            exercise['questions'] = [questions[question_index]]
            answer_value = answers[0] if isinstance(answers, list) and answers else ''
            answers = [answer_value]

        result = gemini.correct_exercise_answers(exercise, answers, subject, user_lang=user_lang)

        # Track stats
        if question_index is None:
            stats = _get_or_create_stats(request.user)
            stats.exercices_resolus += 1
            stats.minutes_etude += 8
            stats.save(update_fields=['exercices_resolus', 'minutes_etude'])

            # ── Suivi adaptatif — exercice corrigé ─────────────────────────
            try:
                from .learning_tracker import update_subject_mastery, log_learning_event
                _gs = result.get('global_score', 0)
                _ms = result.get('max_score', 1) or 1
                _pct = round(_gs / _ms * 100)
                update_subject_mastery(
                    user=request.user,
                    subject=subject,
                    is_correct=_pct >= 60,
                    question_text=(exercise.get('titre') or exercise.get('enonce') or '')[:200],
                    score_pct=float(_pct),
                )
                log_learning_event(
                    user=request.user,
                    event_type='exercise_corrected',
                    subject=subject,
                    details={'score': _gs, 'max': _ms, 'pct': _pct,
                             'exercise_title': (exercise.get('titre') or '')[:100]},
                    score_pct=float(_pct),
                )
            except Exception as _lt_err:
                print(f"[LEARNING_TRACKER] exercise: {_lt_err}")

        return JsonResponse({'ok': True, **result})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_exam_ai_correct(request):
    """Correction IA d'un examen blanc complet (réponses ouvertes).
    POST: {subject: str, qa_pairs: [{question, student_answer, model_answer, pts, section}]}
    Returns: {corrections:[...], estimated_score, total_pts, global_feedback}
    """
    try:
        import json as _json
        body     = _json.loads(request.body)
        subject  = str(body.get('subject', 'general'))[:50]
        qa_pairs = body.get('qa_pairs', [])
        mise_au_net = str(body.get('mise_au_net', '') or '')[:8000]
        if not isinstance(qa_pairs, list) or not qa_pairs:
            return JsonResponse({'error': 'qa_pairs manquants ou vides'}, status=400)
        # Sanitize each pair
        safe_pairs = []
        for item in qa_pairs[:20]:  # cap at 20 questions
            if not isinstance(item, dict):
                continue
            safe_pairs.append({
                'question':       str(item.get('question', ''))[:600],
                'student_answer': str(item.get('student_answer', '') or '')[:1200],
                'model_answer':   str(item.get('model_answer', '') or '')[:400],
                'pts':            float(item.get('pts', 0) or 0),
                'section':        str(item.get('section', ''))[:80],
            })
        if not safe_pairs:
            return JsonResponse({'error': 'Aucune paire valide'}, status=400)

        # Guest exam usage is counted at launch in api_generate_exam_v2.

        user_lang = _get_user_lang(request)
        result = gemini.correct_exam_open_answers(subject, safe_pairs, user_lang=user_lang, mise_au_net=mise_au_net)
        return JsonResponse({'ok': True, **result})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_teach_exercise(request):
    """
    Génère une explication pédagogique complète sur comment résoudre ce type d'exercice.
    POST: {exercise: {...}, subject: str}
    Retourne: {message: str, chat_url: str} — l'élève est redirigé vers le chat avec ce message préchargé.
    """
    try:
        import json as _json
        body     = _json.loads(request.body)
        exercise = body.get('exercise', {})
        subject  = body.get('subject', 'maths')

        # Génère le message pédagogique
        message = gemini.teach_exercise_type(exercise, subject, user_lang=_get_user_lang(request))

        # Construit l'URL chat avec le message et autostart
        from urllib.parse import urlencode, quote
        chat_params = urlencode({
            'subject': subject,
            'preload': message,
            'autostart': '1',
            'exo_theme': exercise.get('theme', ''),
            'exo_intro': (exercise.get('intro') or exercise.get('enonce', ''))[:200],
        })
        chat_url = f"/chat/?{chat_params}"

        return JsonResponse({'ok': True, 'message': message, 'chat_url': chat_url})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_similar_exercise(request):
    """
    Génère un exercice similaire à celui fourni.
    **NOUVEAU**: Si c'est un vrais exercice BAC (physique), cherche d'autres vrais exercices du same theme.
    POST: {exercise: {...}, subject: str}
    Retourne: {message: str} — texte de l'exercice formaté pour le chat.
    """
    try:
        import json as _json
        body     = _json.loads(request.body)
        exercise = body.get('exercise', {})
        subject  = body.get('subject', 'maths')

        # Si c'est un vrai exercice BAC, cherche des similaires
        if exercise.get('_is_real_bac') and subject == 'physique':
            try:
                from .models import BACExercise
                
                bac_id = exercise.get('_bac_exercise_id')
                if bac_id:
                    current_ex = BACExercise.objects.get(id=bac_id)
                    theme = current_ex.theme
                    chapter = current_ex.chapter
                    
                    # Get similar BAC exercises (same theme, different exam)
                    similar = BACExercise.objects.filter(
                        chapter=chapter,
                        theme=theme
                    ).exclude(id=bac_id).order_by('?')[:1]
                    
                    if similar:
                        sim_ex = similar[0]
                        message = f"**Exercice similaire du Bac {sim_ex.exam_year}**\n\n{sim_ex.content[:1000]}"
                        return JsonResponse({'ok': True, 'message': message})
            except Exception as e:
                print(f"[DEBUG] Similar BAC lookup failed: {e}")

        # Fallback: IA génère un exercice similaire
        message = gemini.generate_similar_exercise(exercise, subject)
        return JsonResponse({'ok': True, 'message': message})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)



def examen_blanc_view(request):
    # Guests: allow 1 exam; after that show signup wall from JS
    if _is_guest(request):
        guest_exam_done = request.session.get('guest_exam_done', 0)
        # For guests, show only main subjects (maths, physique, chimie, svt, francais, philosophie, anglais)
        guest_subjects = {'maths', 'physique', 'chimie', 'svt', 'francais', 'philosophie', 'anglais'}
        exam_mats = {k: v for k, v in MATS.items() if k in guest_subjects}
        return render(request, 'core/examen_blanc.html', {
            'mats': exam_mats,
            'is_guest': True,
            'guest_exam_done': guest_exam_done,
        })
    if not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    user_subjs = _get_user_serie_subjects(request.user)
    exam_mats = {k: v for k, v in MATS.items() if k in user_subjs}
    if not exam_mats:
        exam_mats = dict(MATS)
    return render(request, 'core/examen_blanc.html', {'mats': exam_mats})


def api_examen_blanc_questions(request):
    """Retourne des questions pour un examen blanc depuis les items reconstruits.
    Pipeline :
      1. Pool dissertation + question_texte + production_ecrite depuis JSON reconstruits
      2. Contrôle qualité Groq sur chaque item (corrige ou skip)
      3. Fallback BDD QuizQuestion (auto-seeded) si pool vide
    Paramètres GET :
      subject, count (défaut 20), types (ex: "dissertation,question_texte")
    """
    if not request.user.is_authenticated and not _is_guest(request):
        return JsonResponse({'error': 'login_required'}, status=401)
    try:
        subject    = request.GET.get('subject', 'maths')
        count      = int(request.GET.get('count', 20))
        types_str  = request.GET.get('types', '')
        types_filt = [t.strip() for t in types_str.split(',') if t.strip()] or None

        # ── 1. Pool examen blanc depuis items reconstruits ───────────────────
        # Pour les matières à exercices, utilise aussi les exercices dans l'examen blanc
        EXERCISE_SUBJECTS = {'maths', 'physique', 'chimie', 'svt'}
        if subject in EXERCISE_SUBJECTS and not types_filt:
            pool = pdf_loader.get_exercise_items_pool(subject, size=count * 3)
        else:
            pool = pdf_loader.get_exam_blanc_items_pool(subject, types=types_filt, size=count * 3)

        if pool:
            approved = gemini.quality_check_pool(pool, subject, wanted=count)
            if approved:
                items_out = []
                for item in approved:
                    itype = item.get('type', 'question')
                    year  = item.get('_year', '?')
                    if itype == 'exercice':
                        intro     = item.get('intro', '').strip()
                        questions = [str(q).strip() for q in item.get('questions', []) if str(q).strip()]
                        items_out.append({
                            'type':       'exercice',
                            'enonce':     intro + '\n\n' + '\n'.join(questions),
                            'intro':      intro,
                            'questions':  questions,
                            'theme':      item.get('theme', ''),
                            'difficulte': item.get('difficulte', 'moyen'),
                            'source':     item.get('source', f'Bac Haïti {year}'),
                            '_qc_fixed':  item.get('_qc_fixed', False),
                        })
                    elif itype == 'qcm':
                        opts = item.get('options', [])
                        rc   = item.get('reponse_correcte', 0)
                        try:
                            rc = int(rc)
                        except (ValueError, TypeError):
                            rc = 0
                        items_out.append({
                            'type':             'qcm',
                            'enonce':           item.get('enonce', ''),
                            'options':          opts,
                            'reponse_correcte': rc,
                            'explication':      item.get('explication', ''),
                            'theme':            item.get('theme', ''),
                            'difficulte':       item.get('difficulte', 'moyen'),
                            'source':           item.get('source', f'Bac Haïti {year}'),
                            '_qc_fixed':        item.get('_qc_fixed', False),
                        })
                    else:
                        items_out.append({
                            'type':       itype,
                            'enonce':     item.get('enonce', ''),
                            'texte':      item.get('texte', ''),
                            'reponse':    item.get('reponse', ''),
                            'theme':      item.get('theme', ''),
                            'difficulte': item.get('difficulte', 'moyen'),
                            'source':     item.get('source', f'Bac Haïti {year}'),
                            '_qc_fixed':  item.get('_qc_fixed', False),
                        })
                return JsonResponse({
                    'questions':       items_out,
                    'source':          'json_items',
                    'total_pool':      len(pool),
                    'total_available': len(items_out),
                })

        # ── 2. Fallback BDD QuizQuestion ─────────────────────────────────────
        total_available = QuizQuestion.objects.filter(subject=subject).count()
        if total_available < count:
            total_available = _auto_seed_quiz_questions(subject, target=count + 5)

        if total_available == 0:
            return JsonResponse({'error': 'Aucune question disponible.', 'questions': []}, status=200)

        db_questions = list(QuizQuestion.objects.filter(subject=subject).order_by('?')[:count])
        questions = [q.to_dict() for q in db_questions]
        random.shuffle(questions)
        return JsonResponse({'questions': questions, 'source': 'db', 'total_available': total_available})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.', 'questions': []}, status=500)



def api_generate_exam(request):
    """
    Génère un examen blanc structuré (fill-blank, matching, open, hérédité SVT)
    directement depuis les PDFs d'examens via IA.
    Format réel des épreuves BAC Haïti — pas du MCQ.
    """
    import time as _time
    subject = request.GET.get('subject', 'maths')
    try:
        # Pour SVT : combine hérédité + contenu général (limité pour rester dans le context window)
        if subject == 'svt':
            heredity_text = pdf_loader.get_heredity_context(max_chars=1200)
            general_text  = pdf_loader.get_exam_context(subject, max_chars=800, start_idx=0)
            exam_text = f"--- HÉRÉDITÉ/GÉNÉTIQUE ---\n{heredity_text}\n\n--- SVT GÉNÉRAL ---\n{general_text}"
        else:
            exam_text = pdf_loader.get_exam_context(subject, max_chars=5000)
        if not exam_text:
            return JsonResponse({'error': 'Aucun PDF trouvé pour cette matière.'}, status=404)

        # Retry up to 3 times — model may rate-limit and return empty
        exam_data = {}
        for _attempt in range(3):
            exam_data = gemini.generate_structured_exam(exam_text, subject)
            if exam_data:
                break
            _time.sleep(1.5)

        if not exam_data:
            import traceback; traceback.print_exc()
            return JsonResponse({'error': 'Génération échouée. Le modèle n\'a pas retourné de JSON valide. Réessaie dans quelques secondes.'}, status=500)
        return JsonResponse({'exam': exam_data})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


def api_generate_exam_v2(request):
    """
    Génère un examen blanc BAC Haïti de HAUTE QUALITÉ depuis la base de données.
    Pas de PDFs — l'IA génère du contenu ORIGINAL (dissertations, textes, exercices).
    Garde exactement le format de rendu de la page Examen Blanc.
    """
    import time as _time, traceback as _tb
    subject = request.GET.get('subject', 'maths')
    try:
        if _is_guest(request):
            guest_exam_done = request.session.get('guest_exam_done', 0)
            if guest_exam_done >= 1:
                return JsonResponse({'error': 'guest_limit', 'signup_url': '/signup/'}, status=403)
            # Demo rule: exam is consumed when generation is launched.
            request.session['guest_exam_done'] = guest_exam_done + 1
            request.session.modified = True

        # Pull quality quiz questions as thematic reference
        db_questions = list(QuizQuestion.objects.filter(subject=subject).order_by('?')[:16])

        _user_serie = ''
        try:
            _user_serie = request.user.profile.serie or ''
        except Exception:
            pass

        exam_data = {}
        last_err = ''
        for _attempt in range(3):
            try:
                exam_data = gemini.generate_exam_from_db(subject, quiz_questions=db_questions, user_serie=_user_serie)
                if exam_data and exam_data.get('parts'):
                    break
            except Exception as _e:
                last_err = str(_e)
                _tb.print_exc()
            _time.sleep(1.5)

        if not exam_data or not exam_data.get('parts'):
            # Fallback: use structure_exam.json + PDF context
            try:
                import re as _re2
                _struct_path = os.path.join(settings.BASE_DIR, 'database', 'structure_exam.json')
                struct_ctx = ''
                if os.path.exists(_struct_path):
                    with open(_struct_path, 'r', encoding='utf-8') as _sf:
                        raw = _sf.read()
                    # Map subject key to keyword to extract the right section
                    _subj_kw = {
                        'francais': 'Kreyòl', 'maths': 'Mathématiques',
                        'physique': 'Physique', 'chimie': 'Chimie',
                        'svt': 'SVT', 'philosophie': 'Philosophie',
                        'histoire': 'Histoire', 'informatique': 'Informatique',
                        'anglais': 'Anglais', 'espagnol': 'Espagnol',
                        'economie': 'Économie', 'art': 'Art',
                    }
                    kw = _subj_kw.get(subject, MATS.get(subject, {}).get('label', subject))
                    m = _re2.search(r'##[^#]*' + kw + r'.*?(?=\n##|\Z)', raw, _re2.DOTALL | _re2.IGNORECASE)
                    struct_ctx = m.group(0)[:1200] if m else raw[:1200]

                from . import pdf_loader as _pdf
                exam_text = _pdf.get_exam_context(subject, max_chars=3000)
                combined = (struct_ctx + '\n\n' + exam_text).strip() if exam_text else struct_ctx
                if combined:
                    exam_data = gemini.generate_structured_exam(combined, subject)
            except Exception:
                _tb.print_exc()

        # Garde-fou strict demandé: en maths, Exercice 1 de PARTIE B doit être un exercice d'analyse.
        if exam_data and exam_data.get('parts') and subject == 'maths':
            import re as _re

            def _is_analysis_text(_txt: str) -> bool:
                t = (_txt or '').lower()
                # Règle stricte: Exercice 1 analyse/fonctions, exclure explicitement suites.
                suite_kw = ['suite', 'récurrence', 'recurrence', 'u_n', 'u0', 'u_0', 'u5', 'u_5', 'somme géométrique', 'suite géométrique']
                if any(k in t for k in suite_kw):
                    return False
                kw = ['analyse', 'étude de fonction', 'etude de fonction', 'fonction', 'dérivée', 'derivee', 'asymptote', 'tableau de variations', 'courbe']
                return any(k in t for k in kw)

            try:
                parts = exam_data.get('parts', [])
                part_b = None
                for p in parts:
                    if 'partie b' in str(p.get('label', '')).lower():
                        part_b = p
                        break

                if part_b and part_b.get('sections'):
                    sections = list(part_b['sections'])

                    def _section_blob(sec):
                        texts = [str(sec.get('label', ''))]
                        for it in sec.get('items', []) or []:
                            texts.append(str(it.get('text', '')))
                        return '\n'.join(texts)

                    # Si exo 1 n'est pas analyse, essayer de remonter une section analyse existante en position 1
                    first_blob = _section_blob(sections[0])
                    if not _is_analysis_text(first_blob):
                        idx = None
                        for i in range(1, len(sections)):
                            if _is_analysis_text(_section_blob(sections[i])):
                                idx = i
                                break
                        if idx is not None:
                            sections[0], sections[idx] = sections[idx], sections[0]
                        else:
                            # Aucun exo analyse trouvé: régénération stricte depuis DB
                            db_questions = list(QuizQuestion.objects.filter(subject=subject).order_by('?')[:16])
                            strict_exam = gemini.generate_exam_from_db(subject, quiz_questions=db_questions, user_serie=_user_serie)
                            if strict_exam and strict_exam.get('parts'):
                                exam_data = strict_exam
                                parts = exam_data.get('parts', [])
                                for p in parts:
                                    if 'partie b' in str(p.get('label', '')).lower() and p.get('sections'):
                                        sections = list(p['sections'])
                                        break

                    # Renuméroter les labels Exercice 1..n pour rester cohérent après permutation
                    for i, sec in enumerate(sections, 1):
                        lbl = str(sec.get('label', ''))
                        if lbl.lower().startswith('exercice '):
                            sec['label'] = _re.sub(r'^(Exercice\s+)\d+', rf'\g<1>{i}', lbl, flags=_re.IGNORECASE)

                    part_b['sections'] = sections
            except Exception:
                _tb.print_exc()

        if not exam_data or not exam_data.get('parts'):
            return JsonResponse({
                'error': f'Génération échouée. Réessaie dans quelques secondes.{" (" + last_err[:120] + ")" if last_err else ""}'
            }, status=500)

        return JsonResponse({'exam': exam_data})

    except Exception as e:
        _tb.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)

    return JsonResponse({'exam': exam_data})


# ─────────────────────────────────────────────
# PROGRESSION
# ─────────────────────────────────────────────
def _compute_subject_blended_score(user, subj: str) -> dict:
    """
    Calcule un score composite par matière combinant :
      - Quiz (30%) : moyenne des dernières sessions quiz
      - Exercices (50%) : taux de récupération des erreurs
        → 0 pour 'francais' et 'histoire' — pas de section exercices disponible
      - Cours (20%) : bonus si l'élève a ouvert au moins 1 session de cours
    Retourne un dict avec quiz_avg, exo_pct, course_bonus, blended, quiz_count, exo_total.
    """
    # Subjects with no exercise page: redistribute weights to quiz+cours only
    NO_EXERCISE_SUBJECTS = {'francais', 'histoire', 'informatique', 'art'}

    # ── Quiz ──────────────────────────────────────────────────────────
    sessions = QuizSession.objects.filter(user=user, subject=subj)
    pcts = [round((s.score / s.total) * 100) for s in sessions if s.total and s.total > 0]
    quiz_avg   = round(sum(pcts) / len(pcts)) if pcts else None
    quiz_count = len(pcts)

    # ── Exercices ─────────────────────────────────────────────────────
    exo_pct   = None
    exo_total = 0
    if subj not in NO_EXERCISE_SUBJECTS:
        mistakes = MistakeTracker.objects.filter(user=user, subject=subj)
        exo_total = mistakes.count()
        if exo_total:
            recovering = mistakes.filter(correct_streak__gt=0).count()
            exo_pct = round((recovering / exo_total) * 100)

    # ── Cours ─────────────────────────────────────────────────────────
    has_course = CourseSession.objects.filter(user=user, chapter_subject=subj).exists()
    course_bonus = 20 if has_course else 0

    # ── Blended : quiz 30% + exo 50% + cours 20% ──────────────────────
    if subj in NO_EXERCISE_SUBJECTS:
        # No exercises: quiz 80% + cours 20%
        if quiz_avg is not None:
            blended = round(quiz_avg * 0.80 + course_bonus)
        else:
            blended = course_bonus if has_course else None
    else:
        if quiz_avg is not None and exo_pct is not None:
            blended = round(quiz_avg * 0.30 + exo_pct * 0.50 + course_bonus)
        elif quiz_avg is not None:
            blended = round(quiz_avg * 0.50 + course_bonus)
        elif exo_pct is not None:
            blended = round(exo_pct * 0.80 + course_bonus)
        else:
            blended = course_bonus if has_course else None

    # Cap at 100
    if blended is not None:
        blended = min(100, blended)

    return {
        'quiz_avg':    quiz_avg,
        'quiz_count':  quiz_count,
        'exo_pct':     exo_pct,
        'exo_total':   exo_total,
        'has_course':  has_course,
        'course_bonus': course_bonus,
        'blended':     blended,
    }


def progression_view(request):
    # ── Premium gate ──
    if request.user.is_authenticated:
        from core.premium import is_premium
        if not is_premium(request.user):
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            return render(request, 'core/premium_required.html', {
                'profile': profile,
                'feature': 'Progression',
                'message': 'Le suivi de progression est réservé aux abonnés premium. Upgrade pour suivre tes stats détaillées !',
            })

    if not request.user.is_authenticated:
        if _is_guest(request):
            from types import SimpleNamespace
            g = _GUEST_DEMO
            mats_extended = {}
            for k, v in MATS.items():
                mats_extended[k] = dict(v)
                mats_extended[k]['quiz_score'] = g['quiz_scores'].get(k)
                mats_extended[k]['sessions'] = random.randint(1, 4)
                mats_extended[k]['exo_count'] = random.randint(0, 3)
                mats_extended[k]['quiz_avg'] = g['quiz_scores'].get(k)
                mats_extended[k]['exo_pct'] = random.randint(30, 85)
                mats_extended[k]['has_course'] = True
                mats_extended[k]['exo_total'] = random.randint(0, 5)
            mock_profile = SimpleNamespace(streak=g['streak'], school='', serie='', avatar=None)
            mock_stats = SimpleNamespace(exercices_resolus=7, quiz_completes=3, minutes_etude=135)
            return render(request, 'core/progression.html', {
                'is_guest': True,
                'mats': mats_extended,
                'diag_scores': {},
                'heures_etude': g['heures_etude'],
                'minutes_rest': g['minutes_rest'],
                'avg_score': g['avg_score'],
                'bac_score': g['bac_score'],
                'bac_gap_pass': g['bac_gap_pass'],
                'bac_gap_target': g['bac_gap_target'],
                'stats': mock_stats,
                'profile': mock_profile,
                'quiz_sessions': [],
                'user_serie_subjects': g['user_serie_subjects'],
            })
        return redirect('/login/?next=' + request.get_full_path())
    _update_streak(request.user)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    stats       = _get_or_create_stats(request.user)
    diag_scores  = {d.subject: d.score for d in DiagnosticResult.objects.filter(user=request.user)}
    quiz_sessions = QuizSession.objects.filter(user=request.user).order_by('-completed_at')[:10]

    mats_extended = {}
    for k, v in MATS.items():
        sc = _compute_subject_blended_score(request.user, k)
        mats_extended[k] = dict(v)
        mats_extended[k]['sessions']    = sc['quiz_count']
        mats_extended[k]['exo_count']   = sc['exo_total']
        mats_extended[k]['quiz_score']  = sc['blended']    # blended replaces old quiz-only score
        mats_extended[k]['quiz_avg']    = sc['quiz_avg']
        mats_extended[k]['exo_pct']     = sc['exo_pct']
        mats_extended[k]['has_course']  = sc['has_course']

    heures_etude = stats.minutes_etude // 60
    minutes_rest = stats.minutes_etude % 60

    # Estimation BAC sur 1900
    blended_scores = [mats_extended[k]['quiz_score'] for k in mats_extended if mats_extended[k]['quiz_score'] is not None]
    avg_blended = round(sum(blended_scores) / len(blended_scores)) if blended_scores else 0

    # Estimation BAC sur 1900 — coefficient-weighted (même formule que le dashboard)
    # Utilise les scores quiz en priorité, avec fallback sur les scores diagnostics
    # (identique à la logique du dashboard pour éviter les divergences)
    try:
        _prog_serie_key = profile.serie or 'SVT'
        _prog_coeffs = SERIES.get(_prog_serie_key, SERIES['SVT'])['subjects']
        _prog_total_c = sum(_prog_coeffs.values())
        _prog_weighted = 0.0
        for _s, _c in _prog_coeffs.items():
            _score_val = (
                mats_extended[_s]['quiz_score']
                if _s in mats_extended and mats_extended[_s]['quiz_score'] is not None
                else diag_scores.get(_s)
            )
            if _score_val is not None:
                _prog_weighted += (_score_val / 100.0) * _c
        bac_score = round((_prog_weighted / _prog_total_c) * 1900) if _prog_total_c else round(avg_blended / 100 * 1900)
    except Exception:
        bac_score = round(avg_blended / 100 * 1900)

    _prog_user_serie_subjects = list(SERIES.get(profile.serie or 'SVT', SERIES['SVT'])['subjects'].keys())

    context = {
        'mats': mats_extended,
        'diag_scores': diag_scores,
        'quiz_sessions': quiz_sessions,
        'profile': profile,
        'stats': stats,
        'heures_etude': heures_etude,
        'minutes_rest': minutes_rest,
        'avg_score': avg_blended,
        'bac_score': bac_score,
        'user_serie_subjects': _prog_user_serie_subjects,
    }

    # Maîtrise adaptive + résumés de chat pour la page Progression
    try:
        from .learning_tracker import get_study_recommendations
        _serie_subjs_set = set(_prog_user_serie_subjects)
        masteries = [m for m in SubjectMastery.objects.filter(user=request.user).order_by('-mastery_score') if m.subject in _serie_subjs_set]
        # Nettoyer les noms de topics pour l'affichage
        for m in masteries:
            if m.weak_topics:
                m.weak_topics = [_clean_topic_name(t) for t in m.weak_topics]
        chat_summaries = list(ChatSessionSummary.objects.filter(user=request.user).order_by('-created_at')[:5])
        study_recs = [r for r in get_study_recommendations(request.user) if r['subject'] in _serie_subjs_set]
        context['masteries']     = masteries
        context['chat_summaries'] = chat_summaries
        context['study_recs']    = study_recs
    except Exception:
        context['masteries']      = []
        context['chat_summaries'] = []
        context['study_recs']     = []

    return render(request, 'core/progression.html', context)


# ─────────────────────────────────────────────
# PROFIL
# ─────────────────────────────────────────────
def profil_view(request):
    from django.contrib.auth import update_session_auth_hash
    from django.contrib import messages as django_messages
    if _is_guest(request) or not request.user.is_authenticated:
        if _is_guest(request):
            from types import SimpleNamespace
            g = _GUEST_DEMO
            mock_profile = SimpleNamespace(
                streak=g['streak'], school='—', serie='Série A', avatar=None,
                school_real=None, level='Terminale',
            )
            mock_stats = SimpleNamespace(exercices_resolus=7, quiz_completes=3)
            return render(request, 'core/profil.html', {
                'is_guest': True, 'profile': mock_profile, 'stats': mock_stats,
            })
        return redirect('/login/?next=' + request.get_full_path())
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    stats       = _get_or_create_stats(request.user)

    if request.method == 'POST':
        profile.school = request.POST.get('school', profile.school)
        profile.level  = request.POST.get('level', profile.level)
        if request.FILES.get('avatar'):
            profile.avatar = request.FILES['avatar']
        profile.save()
        # Update User fields
        u = request.user
        u.first_name = request.POST.get('first_name', u.first_name)
        u.last_name  = request.POST.get('last_name',  u.last_name)
        # Email is not editable for security
        pass  # email change disabled
        # Password change — no old password required
        new1 = request.POST.get('new_password1', '')
        new2 = request.POST.get('new_password2', '')
        if new1 and new1 == new2:
            u.set_password(new1)
            u.save()
            update_session_auth_hash(request, u)
            django_messages.success(request, 'Mot de passe mis à jour !')
        elif new1 and new1 != new2:
            django_messages.error(request, 'Les mots de passe ne correspondent pas.')
        else:
            u.save()
        django_messages.success(request, 'Profil mis à jour !')
        return redirect('profil')

    return render(request, 'core/profil.html', {'profile': profile, 'stats': stats})


@login_required
def api_avatar_upload(request):
    """Upload avatar instantly — called via AJAX from profil.html on file select."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    if not request.FILES.get('avatar'):
        return JsonResponse({'ok': False, 'error': 'No file'}, status=400)

    file = request.FILES['avatar']
    # Basic size guard (5 MB)
    if file.size > 5 * 1024 * 1024:
        return JsonResponse({'ok': False, 'error': 'Fichier trop grand (max 5 Mo)'}, status=400)
    # Basic type guard
    if not file.content_type.startswith('image/'):
        return JsonResponse({'ok': False, 'error': 'Format invalide'}, status=400)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    # Destroy old avatar on Cloudinary before replacing
    if profile.avatar:
        try:
            import cloudinary.uploader
            cloudinary.uploader.destroy(profile.avatar.public_id)
        except Exception:
            pass
    profile.avatar = file
    profile.save(update_fields=['avatar'])
    return JsonResponse({'ok': True, 'url': profile.avatar.url})


# ─────────────────────────────────────────────
# HISTORIQUE DES CONVERSATIONS
# ─────────────────────────────────────────────
@login_required
def historique_view(request):
    subject_filter = request.GET.get('subject', '')
    search_q       = request.GET.get('q', '').strip()

    qs = ChatMessage.objects.filter(user=request.user, role='user')
    if subject_filter:
        qs = qs.filter(subject=subject_filter)
    if search_q:
        qs = qs.filter(content__icontains=search_q)

    # Group messages by session_key (conversation thread)
    all_messages = list(qs.order_by('-created_at')[:200])
    sessions_dict = {}
    for msg in all_messages:
        key = msg.session_key or f'legacy_{msg.pk}'
        if key not in sessions_dict:
            sessions_dict[key] = []
        sessions_dict[key].append(msg)

    # Build conversation summary list
    conversations = []
    for key, msgs in sessions_dict.items():
        first_msg = msgs[-1]  # oldest
        last_msg  = msgs[0]   # newest (ordered by -created_at)
        conversations.append({
            'session_key': key,
            'subject':     first_msg.subject,
            'preview':     first_msg.content[:100],
            'count':       len(msgs),
            'date':        first_msg.created_at,
        })
    conversations.sort(key=lambda x: x['date'], reverse=True)

    # Stats per subject
    subject_counts = {}
    for m in MATS:
        subject_counts[m] = ChatMessage.objects.filter(
            user=request.user, subject=m, role='user'
        ).count()

    return render(request, 'core/historique.html', {
        'conversations':   conversations,
        'subject_filter':  subject_filter,
        'search_q':        search_q,
        'mats':            MATS,
        'subject_counts':  subject_counts,
        'total_messages':  ChatMessage.objects.filter(user=request.user, role='user').count(),
    })


@login_required
def conversation_detail(request, session_key):
    """Affiche tous les messages d’une conversation."""
    messages = ChatMessage.objects.filter(
        user=request.user, session_key=session_key
    ).order_by('created_at')
    if not messages.exists():
        return redirect('historique')
    subject = messages.first().subject
    return render(request, 'core/conversation_detail.html', {
        'messages': messages,
        'session_key': session_key,
        'subject': subject,
        'subject_label': MATS.get(subject, {}).get('label', subject.capitalize()),
        'mats': MATS,
    })


# ─────────────────────────────────────────────
# FICHES MÉMO (FLASHCARDS)
# ─────────────────────────────────────────────
def fiches_view(request):
    from django.utils import timezone
    subject = request.GET.get('subject', 'maths')

    # ── Premium gate ──
    if request.user.is_authenticated:
        from core.premium import is_premium
        if not is_premium(request.user):
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            return render(request, 'core/premium_required.html', {
                'profile': profile,
                'feature': 'Fiches Mémo',
                'message': 'Les fiches mémo sont réservées aux abonnés premium. Upgrade pour réviser avec des fiches intelligentes !',
            })

    if not request.user.is_authenticated:
        if _is_guest(request):
            # Show real flashcards (no progress tracking) — limited to 6 visible
            flashcards = list(Flashcard.objects.filter(subject=subject).order_by('?')[:6])
            if flashcards:
                cards_data = [{
                    'id': fc.id, 'question': fc.question, 'answer': fc.answer,
                    'hint': fc.hint, 'difficulty': fc.difficulty, 'status': 'new', 'source': 'flashcard',
                } for fc in flashcards]
            else:
                # Fallback: use hardcoded demo flashcards for this subject
                demo_fcs = _GUEST_DEMO['demo_flashcards'].get(subject,
                    _GUEST_DEMO['demo_flashcards'].get('maths', []))
                cards_data = [{
                    'id': f'demo_{i}', 'question': fc['question'], 'answer': fc['answer'],
                    'hint': fc['hint'], 'difficulty': fc['difficulty'], 'status': 'new', 'source': 'flashcard',
                } for i, fc in enumerate(demo_fcs)]
            return render(request, 'core/fiches.html', {
                'subject': subject, 'mats': MATS,
                'cards': cards_data, 'cards_json': json.dumps(cards_data),
                'known': 0, 'review': 0, 'total': len(cards_data),
                'mistakes_count': 0, 'is_guest': True,
                'user_serie_subjects': list(MATS.keys()),
            })
        return redirect('/login/?next=' + request.get_full_path())

    # Load existing flashcards for this subject
    flashcards = list(Flashcard.objects.filter(subject=subject))

    # Get user progress
    progress_qs = FlashcardProgress.objects.filter(
        user=request.user, flashcard__subject=subject
    ).select_related('flashcard')
    progress_map = {p.flashcard_id: p.status for p in progress_qs}

    # Stats
    known  = sum(1 for s in progress_map.values() if s == 'known')
    review = sum(1 for s in progress_map.values() if s == 'review')

    cards_data = [{
        'id':         fc.id,
        'question':   fc.question,
        'answer':     fc.answer,
        'hint':       fc.hint,
        'difficulty': fc.difficulty,
        'status':     progress_map.get(fc.id, 'new'),
        'source':     'flashcard',
    } for fc in flashcards]

    # Add MistakeTracker cards (failed quiz questions as review cards)
    today = timezone.now().date()
    mistakes = MistakeTracker.objects.filter(
        user=request.user,
        subject=subject,
        mastered=False,
    ).order_by('next_review', '-wrong_count')[:30]

    mistake_cards = []
    for m in mistakes:
        opts = m.options if isinstance(m.options, list) else []
        correct_opt = ''
        if opts and isinstance(m.reponse_correcte, int) and 0 <= m.reponse_correcte < len(opts):
            correct_opt = opts[m.reponse_correcte]
        # Use explanation as answer; show correct option as header if available
        if m.explication:
            answer_text = m.explication
            hint_text = correct_opt  # correct option shown as hint/header
        elif correct_opt:
            answer_text = correct_opt
            hint_text = ''
        else:
            answer_text = f'Option {m.reponse_correcte + 1}'
            hint_text = ''

        mistake_cards.append({
            'id':          f'mistake_{m.id}',
            'question':    m.enonce,
            'answer':      answer_text,
            'hint':        hint_text,
            'difficulty':  'difficile',
            'status':      'review',
            'source':      'mistake',
            'wrong_count': m.wrong_count,
            'due':         m.next_review <= today,
        })

    # Count unmastered mistakes for the "generate from errors" button
    mistakes_count = MistakeTracker.objects.filter(
        user=request.user, subject=subject, mastered=False
    ).count()

    all_cards = cards_data + mistake_cards

    _fiches_user_subjs = _get_user_serie_subjects(request.user)
    return render(request, 'core/fiches.html', {
        'subject':        subject,
        'mats':           MATS,
        'cards':          all_cards,
        'cards_json':     json.dumps(all_cards),
        'known':          known,
        'review':         review + len(mistake_cards),
        'total':          len(all_cards),
        'mistakes_count': mistakes_count,
        'user_serie_subjects': list(_fiches_user_subjs),
    })


@login_required
@require_POST
def api_generate_fiches(request):
    """Génère des fiches mémo pour une matière depuis les PDFs."""
    data, err = _parse_json_body(request)
    if err:
        return err
    subject = data.get('subject', 'maths')
    count   = min(int(data.get('count', 8)), 15)

    pdf_ctx = pdf_loader.get_course_context(subject, max_chars=3000)
    user_profile = gemini.build_user_learning_profile(request.user)
    from django.core.cache import cache as _dj_cache
    _flash_key = f'flashcards_{request.user.pk}_{subject}_{count}'
    raw = _dj_cache.get(_flash_key)
    if raw is None:
        raw = gemini.generate_flashcards(subject, pdf_ctx, count, user_profile=user_profile)
        if raw:
            _dj_cache.set(_flash_key, raw, 7200)  # 2h cache

    saved = []
    for fc in raw:
        obj = Flashcard.objects.create(
            subject=subject,
            question=fc['question'],
            answer=fc['answer'],
            hint=fc.get('hint', ''),
            difficulty=fc.get('difficulty', 2),
            source=fc.get('source', 'ai'),
        )
        saved.append({
            'id': obj.id, 'question': obj.question, 'answer': obj.answer,
            'hint': obj.hint, 'difficulty': obj.difficulty, 'status': 'new',
        })
    return JsonResponse({'ok': True, 'cards': saved})


@login_required
@require_POST
def api_flashcard_status(request):
    """Met à jour le statut d’une fiche (new/review/known)."""
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    fc_id  = data.get('id')
    status = data.get('status', 'new')
    if status not in ('new', 'review', 'known'):
        return JsonResponse({'error': 'Invalid status'}, status=400)
    try:
        fc = Flashcard.objects.get(pk=fc_id)
    except Flashcard.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    FlashcardProgress.objects.update_or_create(
        user=request.user, flashcard=fc,
        defaults={'status': status}
    )
    return JsonResponse({'ok': True})


# ─────────────────────────────────────────────
# PLAN DE RÉVISION IA
# ─────────────────────────────────────────────
def plan_view(request):
    # ── Premium gate ──
    if request.user.is_authenticated:
        from core.premium import is_premium
        if not is_premium(request.user):
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            return render(request, 'core/premium_required.html', {
                'profile': profile,
                'feature': 'Plan de révision',
                'message': 'Le plan de révision personnalisé est réservé aux abonnés premium. Upgrade pour un planning IA sur mesure !',
            })

    if not request.user.is_authenticated:
        if _is_guest(request):
            # Build a mock plan object with .content matching the template's expected structure
            from types import SimpleNamespace
            demo_latest_plan = SimpleNamespace(content=_GUEST_DEMO['plan_content'])
            return render(request, 'core/plan.html', {
                'is_guest': True, 'mats': MATS,
                'plans': [], 'latest_plan': demo_latest_plan,
                'diag_scores': _GUEST_DEMO['quiz_scores'], 'profile': None,
            })
        return redirect('/login/?next=' + request.get_full_path())
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    plans = RevisionPlan.objects.filter(user=request.user)[:5]
    diag_scores = {d.subject: d.score for d in DiagnosticResult.objects.filter(user=request.user)}
    return render(request, 'core/plan.html', {
        'plans':       plans,
        'latest_plan': plans.first(),
        'mats':        MATS,
        'diag_scores': diag_scores,
        'profile':     profile,
    })


@login_required
@require_POST
def api_generate_plan(request):
    """Génère un plan de révision IA — calcule automatiquement les semaines jusqu'au Bac (fin juillet)."""
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    # Calculer les semaines restantes jusqu'à fin juillet
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    bac_date = _date(today.year if today.month <= 7 else today.year + 1, 7, 31)
    weeks = max(2, min(26, round((bac_date - today).days / 7)))
    # Allow manual override (for testing)
    if data.get('weeks'):
        weeks = max(2, min(26, int(data['weeks'])))

    try:
        serie_key = request.user.profile.serie or 'SVT'
    except Exception:
        serie_key = 'SVT'

    diag_scores = {d.subject: d.score for d in DiagnosticResult.objects.filter(user=request.user)}

    plan_data = None

    # Check cache first (6h TTL)
    from django.core.cache import cache as _dj_cache
    import hashlib as _hashlib
    _scores_hash = _hashlib.md5(str(sorted(diag_scores.items())).encode()).hexdigest()[:8]
    _plan_cache_key = f'rev_plan_{request.user.pk}_{serie_key}_{weeks}_{_scores_hash}'
    plan_data = _dj_cache.get(_plan_cache_key)
    if plan_data:
        return JsonResponse({'plan': plan_data})

    # Attempt 1: full profile
    try:
        user_profile = gemini.build_user_learning_profile(request.user)
        plan_data = gemini.generate_revision_plan(serie_key, diag_scores, weeks, user_profile=user_profile, user_lang=_get_user_lang(request))
    except Exception as _e:
        import logging as _lg, traceback as _tb
        _lg.getLogger(__name__).error('generate_revision_plan error (attempt 1): %s\n%s', _e, _tb.format_exc())

    # Attempt 2: without user profile (shorter prompt) if first attempt failed
    if not plan_data:
        try:
            plan_data = gemini.generate_revision_plan(serie_key, diag_scores, weeks, user_profile='', user_lang=_get_user_lang(request))
        except Exception as _e2:
            import logging as _lg, traceback as _tb
            _lg.getLogger(__name__).error('generate_revision_plan error (attempt 2): %s\n%s', _e2, _tb.format_exc())

    # Attempt 3: static fallback plan if AI completely fails
    if plan_data:
        _dj_cache.set(_plan_cache_key, plan_data, 21600)  # 6h cache

    if not plan_data:
        _user_subjs = list(SERIES.get(serie_key, SERIES['SVT'])['subjects'].keys())
        _fallback_days_pool = []
        for _si, _subj in enumerate(_user_subjs):
            _lbl = MATS.get(_subj, {}).get('label', _subj)
            _prio = 'high' if diag_scores.get(_subj, 50) < 50 else ('medium' if diag_scores.get(_subj, 50) < 70 else 'low')
            _fallback_days_pool.append({'day': ['Lundi','Mardi','Mercredi','Jeudi','Vendredi'][_si % 5], 'subject': _subj, 'task': f'Réviser {_lbl} — chapitres clés', 'duration_min': 60, 'priority': _prio})
        _fallback_weeks = []
        for _wi in range(min(weeks, 4)):
            _start = (_wi * 5) % len(_fallback_days_pool)
            _wdays = [_fallback_days_pool[(_start + d) % len(_fallback_days_pool)] for d in range(5)]
            _fallback_weeks.append({'label': f'Semaine {_wi + 1}', 'focus': 'Révision générale', 'days': _wdays})
        plan_data = {'summary': 'Plan de révision généré automatiquement. Concentre-toi sur tes matières les plus faibles.', 'weeks': _fallback_weeks}

    plan = RevisionPlan.objects.create(
        user=request.user,
        serie=serie_key,
        content=plan_data,
    )
    return JsonResponse({'ok': True, 'plan_id': plan.id, 'plan': plan_data})


# ─────────────────────────────────────────────
# ANALYSE QUIZ + BOOKMARKS
# ─────────────────────────────────────────────
@login_required
@require_POST
def api_analyse_quiz(request):
    """Analyse post-quiz : IA explique les erreurs et donne des conseils."""
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    session_id = data.get('session_id')
    details    = data.get('details', [])
    subject    = data.get('subject', '')
    try:
        serie_key = request.user.profile.serie or 'SVT'
    except Exception:
        serie_key = 'SVT'

    user_profile = gemini.build_user_learning_profile(request.user)
    result = gemini.analyse_quiz_mistakes(subject, details, serie_key, user_profile=user_profile)

    # Save analysis if session exists
    if session_id:
        try:
            session = QuizSession.objects.get(pk=session_id, user=request.user)
            QuizAnalysis.objects.update_or_create(
                session=session,
                defaults={'content': result.get('analysis',''), 'weak_tags': result.get('weak_tags',[])}
            )
        except QuizSession.DoesNotExist:
            pass

    return JsonResponse({'ok': True, **result})


@login_required
@require_POST
def api_bookmark_toggle(request):
    """Ajoute ou retire une question des favoris."""
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    subject = data.get('subject', '')
    enonce  = data.get('enonce', '')
    existing = BookmarkedQuestion.objects.filter(
        user=request.user, enonce=enonce
    ).first()
    if existing:
        existing.delete()
        return JsonResponse({'ok': True, 'action': 'removed'})
    BookmarkedQuestion.objects.create(
        user=request.user,
        subject=subject,
        enonce=enonce,
        options=data.get('options', []),
        reponse_correcte=data.get('reponse_correcte', 0),
        explication=data.get('explication', ''),
    )
    return JsonResponse({'ok': True, 'action': 'added'})


def bookmarks_view(request):
    # ── Premium gate ──
    if request.user.is_authenticated:
        from core.premium import is_premium
        if not is_premium(request.user):
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            return render(request, 'core/premium_required.html', {
                'profile': profile,
                'feature': 'Favoris',
                'message': 'Les favoris sont réservés aux abonnés premium. Upgrade pour sauvegarder tes questions préférées !',
            })

    if not request.user.is_authenticated:
        if _is_guest(request):
            # Demo bookmarks: real quiz questions from varied subjects
            from .models import QuizQuestion as _QQ
            import itertools
            demo_subjects = ['physique', 'chimie', 'svt', 'philosophie', 'maths', 'francais']
            bk_list = []
            fake_pk = 1
            for subj in demo_subjects:
                qs = list(_QQ.objects.filter(subject=subj).order_by('?')[:1])
                for q in qs:
                    bk_list.append({
                        'pk': fake_pk,
                        'enonce': q.enonce,
                        'subject': q.subject,
                        'options': q.options if isinstance(q.options, list) else [],
                        'reponse_correcte': q.reponse_correcte if isinstance(q.reponse_correcte, int) else 0,
                        'explication': q.explication or '',
                        'created_at': date.today(),
                        'has_math': any(c in (q.enonce or '') for c in ['$', '\\', '≤', '≥', '∑', '∫', '√', 'π']),
                    })
                    fake_pk += 1
            return render(request, 'core/bookmarks.html', {
                'bookmarks': bk_list, 'subject_filter': '',
                'mats': MATS, 'is_guest': True,
                'user_serie_subjects': list(MATS.keys()),
            })
        return redirect('/login/?next=' + request.get_full_path())
    bookmarks = BookmarkedQuestion.objects.filter(user=request.user).order_by('-created_at')
    subject_filter = request.GET.get('subject', '')
    if subject_filter:
        bookmarks = bookmarks.filter(subject=subject_filter)
    _bm_user_subjs = _get_user_serie_subjects(request.user)
    return render(request, 'core/bookmarks.html', {
        'bookmarks': bookmarks,
        'subject_filter': subject_filter,
        'mats': MATS,
        'user_serie_subjects': list(_bm_user_subjs),
    })


# ─────────────────────────────────────────────
# GUEST: set serie
# ─────────────────────────────────────────────
def api_guest_set_serie(request):
    if not _is_guest(request):
        return JsonResponse({'ok': False, 'error': 'not a guest'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    try:
        data = json.loads(request.body)
        serie = str(data.get('serie', '')).upper()
    except Exception:
        return JsonResponse({'ok': False, 'error': 'invalid json'}, status=400)
    VALID_SERIES = {'A', 'B', 'C', 'D', 'F', 'G'}
    if serie not in VALID_SERIES:
        return JsonResponse({'ok': False, 'error': 'invalid serie'}, status=400)
    request.session['guest_serie'] = serie
    request.session.modified = True
    return JsonResponse({'ok': True, 'serie': serie})


# ─────────────────────────────────────────────
# API STATS (pour les graphiques Chart.js)
# ─────────────────────────────────────────────
def api_stats(request):
    """Retourne les données pour les graphiques de progression."""
    # Guest demo mode
    if _is_guest(request):
        import datetime
        today = datetime.date.today()
        fake_timeline = []
        scores = [52, 58, 55, 63, 67]
        subj_labels = ['SVT', 'SVT', 'Maths', 'Physique', 'SVT']
        for i, (score, subj) in enumerate(zip(scores, subj_labels)):
            day = today - datetime.timedelta(days=4 - i)
            fake_timeline.append({'date': day.strftime('%d/%m'), 'subject': subj, 'pct': score})
        radar = [
            {'subject': 'SVT',         'score': 65},
            {'subject': 'Physique',    'score': 58},
            {'subject': 'Chimie',      'score': 50},
            {'subject': 'Maths',       'score': 72},
            {'subject': 'Kreyòl',      'score': 80},
            {'subject': 'Philosophie', 'score': 60},
        ]
        return JsonResponse({'timeline': fake_timeline, 'radar': radar, 'fc_progress': {}})
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'auth required'}, status=401)
    subject = request.GET.get('subject', '')

    # Filtrer par la série de l'utilisateur
    user_subjs = _get_user_serie_subjects(request.user)

    # Évolution des scores quiz dans le temps (30 derniers)
    sessions_qs = QuizSession.objects.filter(user=request.user)
    if subject:
        sessions_qs = sessions_qs.filter(subject=subject)
    sessions_qs = sessions_qs.order_by('completed_at')[:30]

    timeline = [{
        'date':    s.completed_at.strftime('%d/%m'),
        'subject': s.subject,
        'pct':     s.get_percentage(),
    } for s in sessions_qs]

    # Radar chart data — blended score per subject (quiz + exercises + course)
    # Filtré par la série de l'utilisateur
    radar = []
    for subj, info in MATS.items():
        if subj not in user_subjs:
            continue
        sc = _compute_subject_blended_score(request.user, subj)
        if sc['blended'] is not None:
            score = sc['blended']
        else:
            diag = DiagnosticResult.objects.filter(user=request.user, subject=subj).first()
            score = diag.score if diag else 0
        radar.append({'subject': info['label'], 'score': score})

    # Flashcards progress
    fc_progress = {}
    for subj in MATS:
        total  = Flashcard.objects.filter(subject=subj).count()
        known  = FlashcardProgress.objects.filter(
            user=request.user, flashcard__subject=subj, status='known'
        ).count()
        fc_progress[subj] = {'total': total, 'known': known}

    return JsonResponse({
        'timeline':    timeline,
        'radar':       radar,
        'fc_progress': fc_progress,
    })


# ─────────────────────────────────────────────
# COACHING IA — analyse complète + conseils
# ─────────────────────────────────────────────

def _clean_topic_name(raw: str) -> str:
    """Nettoie les noms de catégories/topics bruts pour l'affichage pédagogique."""
    import re
    if not raw:
        return raw
    # Retirer du texte entre parenthèses comme (MÉLANGÉES), (MIXTE), (1947-1991), etc.
    cleaned = re.sub(r'\s*\([^)]*\)\s*', ' ', raw).strip()
    # Retirer "QUESTIONS SUPPLÉMENTAIRES" ou "SUPPLEME..." au début
    cleaned = re.sub(r'^QUESTIONS?\s+SUPPL[ÉE]MENTAIRES?\s*:?\s*', '', cleaned, flags=re.I).strip()
    # Retirer les préfixes de chapitre : "Chapitre HU-4 : ", "CHAPITRE 3 — ", "Chap. 2 - ", etc.
    cleaned = re.sub(
        r'^(?:chapitre|chap\.?)\s*[A-Za-z]*-?\d*\s*[:—–\-]\s*',
        '', cleaned, flags=re.I
    ).strip()
    # Convertir TOUT MAJUSCULES en Title Case
    if cleaned == cleaned.upper() and len(cleaned) > 3:
        cleaned = cleaned.title()
    # Retirer les tirets/underscores au début/fin
    cleaned = cleaned.strip('-_ ')
    return cleaned or raw


def _is_generic_topic_name(raw: str) -> bool:
    """Détecte les pseudo-topics vagues (mélangé, questions suppl., etc.)."""
    if not raw:
        return True
    txt = unicodedata.normalize('NFD', str(raw).lower())
    txt = ''.join(ch for ch in txt if unicodedata.category(ch) != 'Mn')
    generic_tokens = (
        'question supplementaire',
        'questions supplementaires',
        'melange',
        'mixte',
        'divers',
        'autres',
        'general',
    )
    return any(tok in txt for tok in generic_tokens)


def _pick_clear_topic(subject: str, preferred: str, mastery=None, has_resources: bool = False, get_subject_chapters_fn=None) -> str:
    """Retourne un libellé de chapitre compréhensible pour l'élève."""
    cleaned = _clean_topic_name(preferred or '')
    if cleaned and not _is_generic_topic_name(cleaned):
        return cleaned

    weak_topics = []
    try:
        weak_topics = [
            _clean_topic_name(w) for w in (getattr(mastery, 'weak_topics', None) or [])
            if _clean_topic_name(w) and not _is_generic_topic_name(_clean_topic_name(w))
        ]
    except Exception:
        weak_topics = []
    if weak_topics:
        return weak_topics[0]

    if has_resources and get_subject_chapters_fn:
        try:
            chapters = [c for c in get_subject_chapters_fn(subject) if c and not _is_generic_topic_name(c)]
            if chapters:
                return chapters[0]
        except Exception:
            pass

    fallback = {
        'maths': 'Fonctions',
        'physique': 'Mécanique',
        'chimie': 'Réactions chimiques',
        'svt': 'Génétique',
        'philosophie': 'Dissertation philosophique',
        'francais': 'Konpreyansyon tèks',
        'histoire': 'Histoire d’Haïti',
        'anglais': 'Grammar and reading',
        'economie': 'Comptabilité nationale',
    }
    return fallback.get(subject, 'Chapitre principal')

def _generate_coaching_cards(user) -> list:
    """
    Analyse TOUTES les données de l'élève — y compris SubjectMastery, weak_topics,
    recent_errors, LearningEvent — et génère des cartes de coaching ultra-ciblées.
    Aucun appel IA externe — 100% règles Python pour réponse instantanée.
    """
    from datetime import date, timedelta
    from .models import MistakeTracker, AIMemory, QuizAnalysis
    from accounts.models import DiagnosticResult

    cards = []
    today = date.today()

    # Filter to user's serie subjects only
    user_subjs = _get_user_serie_subjects(user)
    serie_mats = {k: v for k, v in MATS.items() if not user_subjs or k in user_subjs}

    # ── Données brutes ─────────────────────────────────────────────────
    profile, _ = UserProfile.objects.get_or_create(user=user)
    stats       = _get_or_create_stats(user)

    # Scores blended par matière
    subject_data = {}
    for subj, info in MATS.items():
        sc = _compute_subject_blended_score(user, subj)
        sessions = list(QuizSession.objects.filter(user=user, subject=subj).order_by('-completed_at')[:15])
        pcts = [round((s.score / s.total) * 100) for s in sessions if s.total]
        trend = 0
        if len(pcts) >= 4:
            recent_avg = sum(pcts[:3]) / 3
            older_avg  = sum(pcts[3:]) / max(1, len(pcts[3:]))
            trend = round(recent_avg - older_avg)
        last_session_date = sessions[0].completed_at.date() if sessions else None
        blended = sc['blended'] if sc['blended'] is not None else 0
        subject_data[subj] = {
            'avg':   blended,
            'quiz_avg': sc['quiz_avg'],
            'trend': trend,
            'count': sc['quiz_count'],
            'last':  last_session_date,
            'pcts':  pcts,
            'exo_total': sc['exo_total'],
            'exo_pct':   sc['exo_pct'],
            'has_course': sc['has_course'],
        }

    # Erreurs dues en révision
    due_by_subj = {}
    total_due = 0
    for subj in serie_mats:
        n = MistakeTracker.objects.filter(
            user=user, subject=subj, mastered=False, next_review__lte=today
        ).count()
        due_by_subj[subj] = n
        total_due += n

    total_mistakes = MistakeTracker.objects.filter(user=user, mastered=False).count()
    total_mastered = MistakeTracker.objects.filter(user=user, mastered=True).count()

    # AIMemory
    memories = list(AIMemory.objects.filter(
        user=user, memory_type__in=['erreur', 'concept']
    ).order_by('-importance', '-updated_at')[:5])

    # ── SubjectMastery — le cœur du nouveau système ───────────────────
    mastery_map = {}  # subject → SubjectMastery
    try:
        for sm in SubjectMastery.objects.filter(user=user):
            if user_subjs and sm.subject not in user_subjs:
                continue
            mastery_map[sm.subject] = sm
    except Exception:
        pass

    # ── Ressources disponibles (catégories quiz) ──────────────────────
    try:
        from .resource_index import get_quiz_categories, get_subject_chapters
        _has_resources = True
    except Exception:
        _has_resources = False

    # =================================================================
    # PRIORITÉ 1 : Cartes basées sur les données adaptatives réelles
    # =================================================================

    # ── RÈGLE A : Topic le plus souvent raté sur une matière (#1 cible) ──
    # Trouve la matière + topic avec le plus d'erreurs récentes enregistrées
    topic_error_counts = {}  # (subject, topic) → count
    for subj, sm in mastery_map.items():
        for err in (sm.recent_errors or []):
            topic = err.get('topic', '').strip()
            if topic:
                key = (subj, topic)
                topic_error_counts[key] = topic_error_counts.get(key, 0) + 1

    if topic_error_counts:
        best_key = max(topic_error_counts, key=topic_error_counts.get)
        s, top_topic = best_key
        sm_topic = mastery_map.get(s)
        top_topic = _pick_clear_topic(s, top_topic, mastery=sm_topic, has_resources=_has_resources, get_subject_chapters_fn=(get_subject_chapters if _has_resources else None))
        err_count = topic_error_counts[best_key]
        info  = MATS.get(s, {})
        label = info.get('label', s)
        color = info.get('color', '#ef4444')

        quiz_url = f'/dashboard/quiz/?subject={s}&chapter={top_topic}'

        cards.append({
            'id':           f'topic_fail_{s}',
            'type':         'topic_fail',
            'icon':         'fas fa-crosshairs',
            'color':        color,
            'priority':     1,
            'title':        f'{label} — {err_count} erreur{"s" if err_count > 1 else ""} sur « {top_topic} »',
            'description':  f'Ton point le plus faible est <strong>{top_topic}</strong> en {label}. '
                            f'L\'IA a identifié ce pattern sur ton historique. Cible ce chapitre maintenant.',
            'action_label': f'Quiz ciblé {label}',
            'action_url':   quiz_url,
            'badge':        f'{err_count}✗',
            'badge_color':  '#ef4444',
        })

    # ── RÈGLE B : Matière avec mastery EMA très faible + erreur récente ──
    # Subjects already covered by cards (prevent duplicates across rules)
    _used_subjects = {c['id'].split('_')[-1] for c in cards}
    critical_mastery = [
        (s, sm) for s, sm in mastery_map.items()
        if sm.mastery_score < 30 and sm.error_count >= 2
        and s not in _used_subjects
    ]
    critical_mastery.sort(key=lambda x: x[1].mastery_score)
    for s, sm in critical_mastery[:1]:
        info  = MATS.get(s, {})
        label = info.get('label', s)
        color = info.get('color', '#f97316')
        weak  = [_clean_topic_name(w) for w in (sm.weak_topics or [])[:2]]
        last_err = (sm.recent_errors or [{}])[0]
        last_q   = (last_err.get('question') or '')[:60]
        chapter_hint = ''
        if _has_resources and weak:
            chapters = get_subject_chapters(s)
            matched_ch = [c for c in chapters if any(w.lower() in c.lower() for w in weak if w)]
            if matched_ch:
                chapter_hint = f' Je te conseille le chapitre <strong>« {matched_ch[0][:50]} »</strong> dans le <u>Cours Interactif</u>.'

        cards.append({
            'id':           f'critical_{s}',
            'type':         'quiz_weak',
            'icon':         'fas fa-exclamation-triangle',
            'color':        '#ef4444',
            'priority':     1,
            'title':        f'{label} — Maîtrise critique : {round(sm.mastery_score)}%',
            'description':  (
                f'Sur {sm.correct_count + sm.error_count} réponses enregistrées, tu as {sm.error_count} erreurs.'
                + (f' Dernière question ratée : « {last_q}… »' if last_q else '')
                + (f' Points faibles : {", ".join(weak)}.' if weak else '')
                + chapter_hint
            ),
            'action_label': 'Cours Interactif',
            'action_url':   f'/dashboard/cours/{s}/' if s not in ('francais',) else '/dashboard/cours/kreyol/',
            'badge':        f'{round(sm.mastery_score)}%',
            'badge_color':  '#ef4444',
        })

    # ── RÈGLE C : Matière en baisse d'EMA (correcte récemment < erreurs) ──
    regressing = [
        (s, sm) for s, sm in mastery_map.items()
        if sm.error_count > 0 and sm.correct_count > 0
        and len(sm.recent_errors or []) >= 3
        and sum(1 for e in (sm.recent_errors or [])[:5]) >= 3  # 3+ erreurs parmi les 5 derniers
        and sm.mastery_score < 60
    ]
    # Compute actual recent error rate per mastery
    def _recent_err_rate(sm):
        recent = (sm.recent_errors or [])[:5]
        return len(recent)
    regressing.sort(key=lambda x: -_recent_err_rate(x[1]))
    _used_subjects = {c['id'].split('_')[-1] for c in cards}
    for s, sm in regressing[:1]:
        if s in _used_subjects:
            continue
        info  = MATS.get(s, {})
        label = info.get('label', s)
        color = info.get('color', '#f59e0b')
        recent5 = (sm.recent_errors or [])[:5]
        top_err_topic = ''
        if recent5:
            topics_in_5 = [e.get('topic','') for e in recent5 if e.get('topic')]
            if topics_in_5:
                from collections import Counter
            raw_top_err_topic = Counter(topics_in_5).most_common(1)[0][0]
            top_err_topic = _pick_clear_topic(s, raw_top_err_topic, mastery=sm, has_resources=_has_resources, get_subject_chapters_fn=(get_subject_chapters if _has_resources else None))
        cards.append({
            'id':           f'regress_{s}',
            'type':         'decline',
            'icon':         'fas fa-chart-line',
            'color':        '#f59e0b',
            'priority':     1,
            'title':        f'{label} — tendance négative récente',
            'description':  (
                f'Tes {len(recent5)} dernières réponses enregistrées incluent {len(recent5)} erreur{"s" if len(recent5)>1 else ""}.'
                + (f' Le sujet problématique : <strong>{top_err_topic}</strong>.' if top_err_topic else '')
                + f' Maîtrise actuelle : {round(sm.mastery_score)}%. Concentre-toi sur ce point avant le prochain quiz.'
            ),
            'action_label': 'Exercices ciblés',
            'action_url':   f'/dashboard/exercices/?subject={s}',
            'badge':        f'{round(sm.mastery_score)}%',
            'badge_color':  '#f59e0b',
        })

    # =================================================================
    # PRIORITÉ 1 (complémentaire) : Règles classiques si pas encore 3 cartes
    # =================================================================

    if total_due > 0 and len(cards) < 4:
        top_subj = max(due_by_subj, key=due_by_subj.get)
        top_info = MATS[top_subj]
        sm_due   = mastery_map.get(top_subj)
        topic_hint = ''
        if sm_due and sm_due.weak_topics:
            topic_hint = f' Focus sur : <strong>{_clean_topic_name(sm_due.weak_topics[0])}</strong>.'
        cards.append({
            'id':           'review_due',
            'type':         'review',
            'icon':         'fas fa-redo',
            'color':        '#f59e0b',
            'priority':     1,
            'title':        f'{total_due} question{"s" if total_due > 1 else ""} à réviser aujourd\'hui',
            'description':  f'La répétition espacée est prête. Commence par <strong>{top_info["label"]}</strong> ({due_by_subj[top_subj]} q.).{topic_hint} Chaque révision consolide la mémoire à long terme.',
            'action_label': f'Réviser {top_info["label"]}',
            'action_url':   f'/dashboard/quiz/?subject={top_subj}',
            'badge':        f'{total_due}',
            'badge_color':  '#f59e0b',
        })

    # ── RÈGLE D : Matière avec weak_topics ET chapitre de cours identifié ──
    if len(cards) < 4:
        for s, sm in sorted(mastery_map.items(), key=lambda x: x[1].mastery_score):
            if any(c['id'].endswith(f'_{s}') for c in cards):
                continue
            if not sm.weak_topics or sm.mastery_score >= 65:
                continue
            info    = MATS.get(s, {})
            label   = info.get('label', s)
            color   = info.get('color', '#06b6d4')
            weak    = [_clean_topic_name(w) for w in (sm.weak_topics or [])[:3]]
            chapter_rec = None
            if _has_resources:
                chapters = get_subject_chapters(s)
                for w in weak:
                    matched_ch = [c for c in chapters if w.lower() in c.lower() or c.lower() in w.lower()]
                    if matched_ch:
                        chapter_rec = matched_ch[0]
                        break

            if chapter_rec:
                cards.append({
                    'id':           f'chapter_{s}',
                    'type':         'chapter',
                    'icon':         'fas fa-book-open',
                    'color':        color,
                    'priority':     2,
                    'title':        f'{label} — lis ce chapitre maintenant',
                    'description':  (
                        f'Ton historique montre des lacunes sur <strong>{", ".join(weak)}</strong>. '
                        f'Le chapitre <strong>« {chapter_rec[:55]} »</strong> couvre exactement ces points. '
                        f'Lis-le dans le Cours Interactif, puis fais un quiz ciblé.'
                    ),
                    'action_label': 'Cours Interactif',
                    'action_url':   f'/dashboard/cours/{s}/',
                    'badge':        f'{round(sm.mastery_score)}%',
                    'badge_color':  color,
                })
                break

    # =================================================================
    # PRIORITÉ 2 : Cartes de contexte et encouragement
    # =================================================================

    # ── Matière inactive + niveau réel connu via mastery ─────────────
    if len(cards) < 5:
        inactive = [
            (s, d) for s, d in subject_data.items()
            if d['last'] and (today - d['last']).days >= 5
        ]
        inactive.sort(key=lambda x: -(today - x[1]['last']).days)
        if inactive:
            s, d = inactive[0]
            if not any(c['id'].endswith(f'_{s}') for c in cards):
                info    = MATS[s]
                days_ago = (today - d['last']).days
                sm       = mastery_map.get(s)
                context_detail = ''
                if sm and sm.weak_topics:
                    context_detail = f' Quand tu reprends, concentre-toi sur : <strong>{_clean_topic_name(sm.weak_topics[0])}</strong>.'
                cards.append({
                    'id':           f'inactive_{s}',
                    'type':         'inactive',
                    'icon':         'fas fa-satellite-dish',
                    'color':        '#8b5cf6',
                    'priority':     2,
                    'title':        f'{info["label"]} — {days_ago} jours sans pratique',
                    'description':  f'La dernière session remonte à {days_ago} jours. La mémoire s\'efface sans pratique régulière — 10 minutes aujourd\'hui valent mieux qu\'1h dans une semaine.{context_detail}',
                    'action_label': f'Quiz {info["label"]}',
                    'action_url':   f'/dashboard/quiz/?subject={s}',
                    'badge':        f'{d["avg"]}%',
                    'badge_color':  MATS[s]['color'],
                })

    # ── Matière améliorée — valider avec un quiz ciblé sur un topic ──
    if len(cards) < 5:
        improving = [(s, d) for s, d in subject_data.items() if d['trend'] > 15 and d['count'] >= 4]
        improving.sort(key=lambda x: -x[1]['trend'])
        if improving:
            s, d = improving[0]
            if not any(c['id'].endswith(f'_{s}') for c in cards):
                info = MATS[s]
                sm   = mastery_map.get(s)
                next_target = ''
                if sm and sm.weak_topics:
                    next_target = f' Prochain objectif : maîtriser <strong>{_clean_topic_name(sm.weak_topics[0])}</strong>.'
                cards.append({
                    'id':           f'improve_{s}',
                    'type':         'improve',
                    'icon':         'fas fa-rocket',
                    'color':        '#10b981',
                    'priority':     2,
                    'title':        f'Super progression en {info["label"]} !',
                    'description':  f'<strong>+{d["trend"]}%</strong> sur tes derniers quiz.{next_target} Continue sur cette lancée — c\'est le bon moment pour consolider.',
                    'action_label': 'Continuer',
                    'action_url':   f'/dashboard/quiz/?subject={s}',
                    'badge':        f'+{d["trend"]}%',
                    'badge_color':  '#10b981',
                })

    # ── Erreur conceptuelle récurrente mémorisée par l'IA ────────────
    if memories and len(cards) < 5:
        mem = memories[0]
        subj_label = MATS.get(mem.subject, {}).get('label', mem.subject) if mem.subject else 'général'
        from urllib.parse import quote as _urlquote
        _mem_preload = _urlquote(
            f"J'ai du mal à comprendre ce concept en {subj_label} : {mem.content[:200]}. "
            f"Explique-moi ce concept simplement, avec un exemple concret, pour que je puisse le maîtriser pour le BAC."
        )
        cards.append({
            'id':           f'memory_{mem.pk}',
            'type':         'memory',
            'icon':         'fas fa-brain',
            'color':        '#a78bfa',
            'priority':     2,
            'title':        f'Point récurrent à maîtriser',
            'description':  f'<em>{mem.content[:120]}{"…" if len(mem.content) > 120 else ""}</em>' +
                            (f'<br><span style="font-size:.78rem;color:var(--t3)">Source : {subj_label}</span>' if mem.subject else ''),
            'action_label': 'Demander à l\'IA',
            'action_url':   f'/dashboard/chat/?subject={mem.subject or "general"}&preload={_mem_preload}',
            'badge':        None,
            'badge_color':  None,
        })

    # ── Matière jamais testée ────────────────────────────────────────
    never_tested = [(s, d) for s, d in subject_data.items() if d['count'] == 0 and s not in mastery_map]
    if never_tested and len(cards) < 5:
        s, _ = never_tested[0]
        info  = MATS[s]
        cards.append({
            'id':           f'never_{s}',
            'type':         'explore',
            'icon':         'fas fa-compass',
            'color':        '#06b6d4',
            'priority':     2,
            'title':        f'Tu n\'as jamais testé {info["label"]}',
            'description':  f'Un premier quiz de 10 questions te donnera immédiatement une idée de ton niveau. 5 minutes chrono — et l\'IA adaptera tes recommandations dès le premier résultat.',
            'action_label': f'Découvrir {info["label"]}',
            'action_url':   f'/dashboard/quiz/?subject={s}',
            'badge':        'Nouveau',
            'badge_color':  '#06b6d4',
        })

    # =================================================================
    # PRIORITÉ 3 : Streak / stats
    # =================================================================
    # Compute weakest subject for generic cards that need a default target
    _weakest_s = None
    if mastery_map:
        _weakest_s = min(mastery_map, key=lambda x: mastery_map[x].mastery_score)
    else:
        _tested = {s: d for s, d in subject_data.items() if d['count'] > 0}
        if _tested:
            _weakest_s = min(_tested, key=lambda x: _tested[x]['avg'])
        elif serie_mats:
            _weakest_s = next(iter(serie_mats), None)
    _weakest_quiz_url = f'/dashboard/quiz/?subject={_weakest_s}' if _weakest_s else '/dashboard/quiz/'

    streak = profile.streak or 0
    if streak == 0 and len(cards) < 5:
        cards.append({
            'id':           'streak_dead',
            'type':         'streak',
            'icon':         'fas fa-fire-alt',
            'color':        '#ef4444',
            'priority':     3,
            'title':        'Série interrompue — recommence aujourd\'hui',
            'description':  'La régularité est la clé du BAC. Même 10 minutes par jour font une différence énorme sur le long terme. Lance un quiz maintenant pour relancer ta série !',
            'action_label': 'Quiz rapide 5 min',
            'action_url':   _weakest_quiz_url,
            'badge':        '0 jours',
            'badge_color':  '#ef4444',
        })
    elif streak >= 7 and len(cards) < 5:
        cards.append({
            'id':           'streak_hot',
            'type':         'streak',
            'icon':         'fas fa-fire',
            'color':        '#f97316',
            'priority':     3,
            'title':        f'{streak} jours de suite — ne t\'arrête pas !',
            'description':  f'Tu es sur une série de <strong>{streak} jours</strong>. Les élèves qui maintiennent une série de 7+ jours obtiennent en moyenne 23% de meilleures notes. Continue !',
            'action_label': 'Maintenir la série',
            'action_url':   _weakest_quiz_url,
            'badge':        f'🔥 {streak}j',
            'badge_color':  '#f97316',
        })

    if total_mastered >= 10 and len(cards) < 5:
        cards.append({
            'id':           'mastered',
            'type':         'celebrate',
            'icon':         'fas fa-medal',
            'color':        '#facc15',
            'priority':     3,
            'title':        f'{total_mastered} questions maîtrisées !',
            'description':  f'Tu as réussi {total_mastered} questions 3× de suite. Il te reste encore <strong>{total_mistakes}</strong> erreurs à consolider. Chaque question maîtrisée = un point gagné au BAC.',
            'action_label': 'Voir ma progression',
            'action_url':   '/dashboard/progression/',
            'badge':        f'✓ {total_mastered}',
            'badge_color':  '#facc15',
        })

    # ── Trier par priorité + limiter à 5 cartes ───────────────────────
    cards.sort(key=lambda c: c['priority'])
    return cards[:5]



def _filter_coaching_cards_by_serie(cards, serie):
    """Filtre les cartes de coaching pour n'afficher que celles des matières de la série."""
    if not serie or serie not in SERIES:
        return cards
    
    serie_subjects = set(SERIES[serie]['subjects'].keys())
    serie_subjects.add('francais')  # Kreyòl toujours inclus
    
    # Map subject keys to MATS keys for card matching
    subj_to_card_name = {
        'maths': 'Mathématiques',
        'physique': 'Physique',
        'chimie': 'Chimie',
        'svt': 'SVT',
        'francais': 'Kreyòl',
        'philosophie': 'Philosophie',
        'anglais': 'Anglais',
        'histoire': 'Sc Social',  # Peut être 'Sc Social' ou 'Histoire'
        'economie': 'Économie',
        'informatique': 'Informatique',
        'art': 'Art',
        'espagnol': 'Espagnol',
    }
    
    filtered = []
    for card in cards:
        card_title = card.get('title', '')
        # Extrait la première partie du titre (matière)
        first_part = card_title.split(' — ')[0] if ' — ' in card_title else card_title.split(' ')[0]
        
        # Vérifie si cette matière est dans la série
        keep_card = False
        for subj in serie_subjects:
            subj_label = MATS.get(subj, {}).get('label', subj)
            if subj_label in card_title or first_part in subj_label:
                keep_card = True
                break
        
        if keep_card:
            filtered.append(card)
    
    return filtered[:3]  # Max 3 cartes par série


def api_coaching_cards(request):
    """Retourne instantanément les cartes de coaching sans appel IA (pour le dashboard)."""
    if _is_guest(request):
        guest_serie = request.GET.get('serie') or request.session.get('guest_serie')
        cards = _GUEST_DEMO['coaching_cards']
        if guest_serie:
            cards = _filter_coaching_cards_by_serie(cards, guest_serie)
        return JsonResponse({'ok': True, 'cards': cards})
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'cards': []}, status=401)
    cards = _generate_coaching_cards(request.user)
    return JsonResponse({'ok': True, 'cards': cards})


def api_coaching(request):
    """Retourne les cartes de coaching + un message IA personnalisé pour la page Progression."""
    if _is_guest(request):
        return JsonResponse({
            'ok': True,
            'cards': _GUEST_DEMO['coaching_cards'],
            'advice': _GUEST_DEMO['coach_advice'],
            'chapter_advice': (
                '<strong>📊 Analyse par chapitre</strong><br><br>'
                '⚠️ <strong>Économie — Comptabilité nationale</strong> : '
                'Ce chapitre représente ~15% du BAC. Tes résultats (48%) montrent des lacunes '
                'sur les agrégats et le PIB. Revois les définitions clés.<br><br>'
                '📈 <strong>SVT — Division cellulaire (Mitose / Méiose)</strong> : '
                'Chapitre incontournable (55%). Travaille les schémas des phases et '
                'les différences entre mitose et méiose.<br><br>'
                '📐 <strong>Maths — Dérivées et applications</strong> : '
                'Avec 58%, concentre-toi sur les règles de dérivation composée '
                'et les études de variations.<br><br>'
                '✅ <strong>Kreyòl (80%)</strong> et <strong>Chimie (72%)</strong> '
                '— Continue sur ta lancée ! Passe aux exercices BAC pour consolider.'
            ),
        })
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'cards': [], 'advice': '', 'chapter_advice': ''}, status=401)
    from datetime import date
    from .models import MistakeTracker, AIMemory
    from django.db.models import Sum

    user  = request.user
    today = date.today()
    cards = _generate_coaching_cards(user)

    # ── Collecte des données élève pour le prompt IA ──────────────────
    profile, _ = UserProfile.objects.get_or_create(user=user)
    stats       = _get_or_create_stats(user)

    # Use blended scores (quiz + exercises + course)
    subject_data = {}
    for subj, info in MATS.items():
        sc = _compute_subject_blended_score(user, subj)
        sessions_qs = QuizSession.objects.filter(user=user, subject=subj).order_by('-completed_at')[:15]
        sessions = list(sessions_qs)
        pcts = [round((s.score / s.total) * 100) for s in sessions if s.total]
        trend = 0
        if len(pcts) >= 4:
            recent_avg = sum(pcts[:3]) / 3
            older_avg  = sum(pcts[3:]) / max(1, len(pcts[3:]))
            trend = round(recent_avg - older_avg)
        last_date = sessions[0].completed_at.date() if sessions else None
        subject_data[subj] = {
            'label':      info['label'],
            'avg':        sc['blended'] if sc['blended'] is not None else 0,
            'quiz_avg':   sc['quiz_avg'],
            'trend':      trend,
            'count':      sc['quiz_count'],
            'last':       last_date,
            'exo_total':  sc['exo_total'],
            'exo_pct':    sc['exo_pct'],
            'has_course': sc['has_course'],
        }

    weak_subjects = sorted(
        [{'label': d['label'], 'avg': d['avg'], 'quiz_avg': d['quiz_avg'],
          'exo_pct': d['exo_pct'], 'has_course': d['has_course'], 'count': d['count']}
         for s, d in subject_data.items() if d['avg'] < 60 and (d['count'] >= 1 or d['exo_total'] > 0 or d['has_course'])],
        key=lambda x: x['avg']
    )
    declining_subjects = sorted(
        [{'label': d['label'], 'trend': d['trend']}
         for s, d in subject_data.items() if d['trend'] < -10 and d['count'] >= 3],
        key=lambda x: x['trend']
    )
    inactive_subjects = sorted(
        [{'label': d['label'], 'days_ago': (today - d['last']).days}
         for s, d in subject_data.items() if d['last'] and (today - d['last']).days >= 5],
        key=lambda x: -x['days_ago']
    )
    never_tested = [d['label'] for s, d in subject_data.items()
                    if d['count'] == 0 and not d['has_course'] and d['exo_total'] == 0]

    mistake_agg = (
        MistakeTracker.objects
        .filter(user=user, mastered=False)
        .values('subject')
        .annotate(n=Sum('wrong_count'))
        .order_by('-n')[:3]
    )
    top_mistake_subjects = [
        MATS.get(m['subject'], {}).get('label', m['subject']) for m in mistake_agg
    ]

    memories = [
        {
            'subject': MATS.get(m.subject, {}).get('label', m.subject or 'général'),
            'content': m.content,
        }
        for m in AIMemory.objects.filter(
            user=user, memory_type__in=['erreur', 'concept']
        ).order_by('-importance', '-updated_at')[:3]
    ]

    # Course sessions per subject (chapters visited)
    course_counts = {}
    for subj in MATS:
        course_counts[subj] = CourseSession.objects.filter(user=user, chapter_subject=subj).count()

    # Compute blended avg for BAC/1900 estimate
    blended_vals = [d['avg'] for d in subject_data.values() if d['avg'] > 0]
    avg_blended = round(sum(blended_vals) / len(blended_vals)) if blended_vals else 0
    bac_score_estimate = round(avg_blended / 100 * 1900)
    bac_gap_pass   = max(0, 950 - bac_score_estimate)   # points to reach 50%
    _usr_bac_target = getattr(profile, 'bac_target', None) or None
    _bac_target_score2 = _usr_bac_target if _usr_bac_target and _usr_bac_target > 950 else None
    bac_gap_target = max(0, _bac_target_score2 - bac_score_estimate) if _bac_target_score2 else None

    # Top mistakes with full details (enonce + theme) for chapter-level advice
    top_mistakes_detail = list(
        MistakeTracker.objects
        .filter(user=user, mastered=False)
        .order_by('-wrong_count')[:8]
        .values('subject', 'enonce', 'theme', 'wrong_count')
    )

    student_data = {
        'first_name':           user.first_name or user.username,
        'streak':               profile.streak or 0,
        'study_minutes':        stats.minutes_etude,
        'quiz_count':           stats.quiz_completes,
        'total_mistakes':       MistakeTracker.objects.filter(user=user, mastered=False).count(),
        'total_mastered':       MistakeTracker.objects.filter(user=user, mastered=True).count(),
        'weak_subjects':        weak_subjects,
        'declining_subjects':   declining_subjects,
        'inactive_subjects':    inactive_subjects,
        'never_tested':         never_tested,
        'top_mistake_subjects': top_mistake_subjects,
        'top_mistakes_detail':  top_mistakes_detail,
        'memories':             memories,
        'course_counts':        course_counts,
        'avg_score':            avg_blended,
        'bac_score':            bac_score_estimate,
        'bac_gap_pass':         bac_gap_pass,
        'bac_gap_target':       bac_gap_target,
    }

    # ── Données de maîtrise adaptative (SubjectMastery) ───────────────
    try:
        mastery_data = [
            {
                'subject': sm.subject,
                'label':   MATS.get(sm.subject, {}).get('label', sm.subject),
                'mastery': round(sm.mastery_score),
                'confidence': sm.confidence_level,
                'correct': sm.correct_count,
                'errors':  sm.error_count,
                'weak_topics': sm.weak_topics[:3],
            }
            for sm in SubjectMastery.objects.filter(user=user).order_by('mastery_score')
        ]
        student_data['mastery_data'] = mastery_data

        # Résumés de chat récents
        recent_summaries = list(ChatSessionSummary.objects.filter(user=user).order_by('-created_at')[:3])
        student_data['recent_chat_summaries'] = [
            {
                'subjects': s.subjects_covered,
                'strengths': s.summary.get('strengths', []),
                'weaknesses': s.summary.get('weaknesses', []),
                'confidence': s.summary.get('confidence', ''),
            }
            for s in recent_summaries
        ]
    except Exception as _me:
        print(f"[api_coaching] mastery load error: {_me}")
        student_data['mastery_data'] = []
        student_data['recent_chat_summaries'] = []

    # ── Appel IA (avec cache persistant) ─────────────────────────────────────────
    from .models import AIProgressCache
    from django.utils import timezone as _tz
    from datetime import timedelta as _td
    
    # Récupérer ou créer le cache persistant
    cache, created = AIProgressCache.objects.get_or_create(user=user)
    
    # Vérifier si le cache est encore valide (24h max)
    is_cache_valid = cache.is_valid and cache.last_updated > (_tz.now() - _td(hours=24))
    
    advice = cache.coaching_advice if is_cache_valid else None
    chapter_advice = cache.chapter_advice if is_cache_valid else None

    if advice is None or chapter_advice is None:
        # Régénérer les données IA
        try:
            if advice is None:
                advice = gemini.generate_coaching_advice(student_data)
                cache.coaching_advice = advice
            
            if chapter_advice is None:
                chapter_advice = gemini.generate_chapter_advice(
                    top_mistakes_detail, weak_subjects, MATS
                )
                cache.chapter_advice = chapter_advice
            
            cache.is_valid = True
            cache.save()
            
        except Exception as e:
            # Fallback : résumé des cartes
            if advice is None:
                advice = '\n\n'.join(
                    f'<strong>{c.get("title","")}</strong>\n{c.get("description","")}'
                    for c in cards[:3]
                )
            if chapter_advice is None:
                chapter_advice = ''
            
            # Marquer le cache comme invalide en cas d'erreur
            cache.is_valid = False
            cache.save()

    return JsonResponse({'ok': True, 'cards': cards, 'advice': advice, 'chapter_advice': chapter_advice})


# ─────────────────────────────────────────────────────────────────────────────
# SMART COACH IA — COACHING ULTRA-PERSONNALISÉ
# ─────────────────────────────────────────────────────────────────────────────

def api_smart_coach(request):
    """
    Coach IA avancé : analyse TOUT (maîtrise, erreurs, chats, événements d'apprentissage)
    et retourne un plan personnalisé avec quiz ciblés, exercices et chapitres recommandés.
    """
    if _is_guest(request):
        return JsonResponse({
            'ok': True,
            'message': "Crée un compte gratuit pour accéder au Coach IA Avancé — il analysera chaque erreur, chaque quiz, chaque session de chat pour te connaître vraiment et te guider précisément !",
            'quiz_picks': [],
            'exercise_recs': [],
            'chapter_recs': [],
        })
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'auth required'}, status=401)

    user = request.user

    from .resource_index import get_full_resource_catalog, get_targeted_questions, COURS_URLS

    # ── Profil de maîtrise complet ────────────────────────────────────────────
    mastery_data = []
    try:
        for sm in SubjectMastery.objects.filter(user=user).order_by('mastery_score'):
            mastery_data.append({
                'subject':         sm.subject,
                'label':           MATS.get(sm.subject, {}).get('label', sm.subject),
                'mastery':         round(sm.mastery_score),
                'confidence':      sm.confidence_level,
                'correct_count':   sm.correct_count,
                'error_count':     sm.error_count,
                'weak_topics':     list(sm.weak_topics or []),
                'mastered_topics': list(sm.mastered_topics or []),
                # Envoie les 20 erreurs les plus récentes à l'IA (éviter prompt trop long)
                'recent_errors':   list(sm.recent_errors or [])[:20],
                'recent_correct':  list(sm.recent_correct or [])[:10],
            })
    except Exception as _e:
        print(f"[smart_coach] mastery error: {_e}")

    # ── Résumés des sessions de chat ──────────────────────────────────────────
    chat_summaries = []
    try:
        for s in ChatSessionSummary.objects.filter(user=user).order_by('-created_at')[:5]:
            chat_summaries.append({
                'subjects':      list(s.subjects_covered or []),
                'strengths':     s.summary.get('strengths', []),
                'weaknesses':    s.summary.get('weaknesses', []),
                'confidence':    s.summary.get('confidence', ''),
                'key_questions': s.summary.get('key_questions', []),
                'observations':  s.summary.get('observations', ''),
            })
    except Exception:
        pass

    # ── Événements d'apprentissage récents ────────────────────────────────────
    learning_events = []
    try:
        from .models import LearningEvent
        for ev in LearningEvent.objects.filter(user=user).order_by('-created_at')[:15]:
            learning_events.append({
                'event_type': ev.event_type,
                'subject':    ev.subject,
                'score_pct':  ev.score_pct,
                'details':    ev.details,
            })
    except Exception:
        pass

    student_data = {
        'first_name':          user.first_name or user.username,
        'mastery_data':        mastery_data,
        'recent_chat_summaries': chat_summaries,
        'learning_events':     learning_events,
    }

    # ── Catalogue de ressources ───────────────────────────────────────────────
    resource_catalog = get_full_resource_catalog()

    # ── Appel IA pour le plan de coaching ────────────────────────────────────
    try:
        plan = gemini.generate_smart_coach_plan(student_data, resource_catalog)
    except Exception as _e:
        print(f"[smart_coach] AI error: {_e}")
        plan = {
            'message':       f"Bonjour {user.first_name or user.username} ! Ton coach IA analyse ton profil — reviens dans un instant.",
            'quiz_picks':    [],
            'exercise_recs': [],
            'chapter_recs':  [],
        }

    # ── Pour chaque quiz_pick : récupérer les vraies questions ciblées ────────
    quiz_picks_enriched = []
    for pick in (plan.get('quiz_picks') or [])[:4]:
        subject    = pick.get('subject', '')
        category   = pick.get('category', '')
        n_q        = min(int(pick.get('n_questions', 8) or 8), 10)
        difficulty = pick.get('difficulty')
        if difficulty in ('tous', 'all', ''):
            difficulty = None

        questions = get_targeted_questions(
            subject=subject,
            topics=[category] if category else [],
            n=n_q,
            difficulty=difficulty,
        )

        quiz_picks_enriched.append({
            'subject':       subject,
            'subject_label': MATS.get(subject, {}).get('label', subject),
            'subject_color': MATS.get(subject, {}).get('color', '#6366f1'),
            'category':      category,
            'reason':        pick.get('reason', ''),
            'n_questions':   len(questions),
            'quiz_url':      f'/dashboard/quiz/?subject={subject}',
            'questions': [
                {
                    'id':          q.get('id', ''),
                    'question':    q.get('question', ''),
                    'options':     q.get('options', []),
                    'correct':     q.get('correct', ''),
                    'explanation': q.get('explanation', ''),
                    'category':    q.get('category', ''),
                    'difficulty':  q.get('difficulty', ''),
                }
                for q in questions
            ],
        })

    # ── Enrichir chapter_recs and exercise_recs avec URLs ────────────────────
    chapter_recs = []
    for rec in (plan.get('chapter_recs') or [])[:3]:
        subject = rec.get('subject', '')
        chapter_recs.append({
            'subject':       subject,
            'subject_label': MATS.get(subject, {}).get('label', subject),
            'subject_color': MATS.get(subject, {}).get('color', '#10b981'),
            'chapter':       rec.get('chapter', ''),
            'reason':        rec.get('reason', ''),
            'url':           COURS_URLS.get(subject, '/dashboard/cours/'),
        })

    exercise_recs = []
    for rec in (plan.get('exercise_recs') or [])[:3]:
        subject = rec.get('subject', '')
        chapter = rec.get('chapter', '')
        reason  = rec.get('reason', '')
        # Chercher un vrai exercice BAC correspondant au chapitre
        real_exo = None
        if subject and chapter:
            try:
                from .resource_index import get_exam_exercise_for_topic
                matches = get_exam_exercise_for_topic(subject, chapter, n=1)
                if matches:
                    real_exo = matches[0]
            except Exception:
                pass
        exercise_recs.append({
            'subject':       subject,
            'subject_label': MATS.get(subject, {}).get('label', subject),
            'subject_color': MATS.get(subject, {}).get('color', '#f59e0b'),
            'chapter':       chapter,
            'reason':        reason,
            'url':           '/dashboard/exercices/',
            # Données de l'exercice trouvé (raw_text sera nettoyé via IA ensuite)
            '_real_exo':    real_exo,  # porteur temporaire, retiré avant JsonResponse
        })

    # ── Nettoyage IA des exercices trouvés (une seule requête groupée) ────────
    to_clean = []
    clean_indices = []
    for i, rec in enumerate(exercise_recs):
        real_exo = rec.pop('_real_exo', None)
        if real_exo and real_exo.get('raw_text') and real_exo.get('theme'):
            to_clean.append({
                'topic':    real_exo['theme'],
                'raw_text': real_exo['raw_text'],
                'exam_name': real_exo.get('exam_name', ''),
                'year':      real_exo.get('year', ''),
            })
            clean_indices.append(i)
            # Stocker les métadonnées dans le rec en attendant
            rec['exo_theme']    = real_exo['theme']
            rec['exo_exam_name'] = real_exo.get('exam_name', '')
            rec['exo_year']      = real_exo.get('year', '')
            rec['exo_cleaned']   = ''  # sera rempli ci-dessous
        else:
            rec['exo_theme']     = None
            rec['exo_exam_name'] = None
            rec['exo_year']      = None
            rec['exo_cleaned']   = ''

    if to_clean:
        try:
            cleaned = gemini.extract_and_clean_exercises(to_clean)
            for idx_in_list, rec_idx in enumerate(clean_indices):
                if idx_in_list < len(cleaned):
                    exercise_recs[rec_idx]['exo_cleaned'] = cleaned[idx_in_list].get('cleaned_text', '')
        except Exception as _ce:
            print(f"[api_smart_coach] exercise clean error: {_ce}")

    return JsonResponse({
        'ok':           True,
        'message':      plan.get('message', ''),
        'quiz_picks':   quiz_picks_enriched,
        'chapter_recs': chapter_recs,
        'exercise_recs': exercise_recs,
    })


def api_mistakes_summary(request):
    """Retourne la liste des erreurs pour la page Progression."""
    if _is_guest(request):
        return JsonResponse({'mistakes': [], 'due': 0})
    if not request.user.is_authenticated:
        return JsonResponse({'mistakes': [], 'due': 0}, status=401)
    from datetime import date
    from .models import MistakeTracker
    today = date.today()
    qs = MistakeTracker.objects.filter(
        user=request.user, mastered=False
    ).order_by('-wrong_count', 'next_review')[:20]
    mistakes = []
    for m in qs:
        next_rev = m.next_review
        if next_rev <= today:
            label = 'aujourd\'hui'
        else:
            delta = (next_rev - today).days
            label = f'dans {delta} jour{"s" if delta > 1 else ""}'
        mistakes.append({
            'question_preview': m.enonce[:100],
            'wrong_count':      m.wrong_count,
            'subject':          MATS.get(m.subject, {}).get('label', m.subject),
            'next_review':      label,
        })
    return JsonResponse({'mistakes': mistakes})


# ─────────────────────────────────────────────
# STUDY PING — timer de session en temps réel
# ─────────────────────────────────────────────
@login_required
@require_POST
def study_ping(request):
    """Appelé toutes les 2 minutes pendant que l'élève est actif."""
    stats = _get_or_create_stats(request.user)
    stats.minutes_etude += 2
    stats.save(update_fields=['minutes_etude'])
    total = stats.minutes_etude
    return JsonResponse({
        'ok': True,
        'heures':  total // 60,
        'minutes': total % 60,
    })


# ─────────────────────────────────────────────
# COURS INTERACTIF
# ─────────────────────────────────────────────

def cours_view(request):
    """Page principale : choisir une matière puis un chapitre (JSON-backed)."""
    if _is_guest(request):
        chapters_by_subject = {}
        for subj, info in MATS.items():
            chapters = _get_cours_chapters(subj)
            # ALL chapters locked for guests — no course reading in demo
            for ch in chapters:
                ch['guest_locked'] = True
                ch['progress_pct'] = 0
            chapters_by_subject[subj] = {
                'info': info,
                'chapters': chapters,
                'count': len(chapters),
            }
        return render(request, 'core/cours.html', {
            'profile': None,
            'chapters_by_subject': chapters_by_subject,
            'recent_sessions': [],
            'mats': MATS,
            'user_serie_subjects': list(MATS.keys()),
            'any_chapters': any(d['count'] > 0 for d in chapters_by_subject.values()),
            'is_guest': True,
        })
    if not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    user_subjs = _get_user_serie_subjects(request.user)

    # Progression par chapitre (visible avant l'ouverture d'un chapitre)
    sessions = CourseSession.objects.filter(
        user=request.user,
        chapter_subject__in=list(MATS.keys()),
        chapter_num__isnull=False,
    ).order_by('-updated_at')

    progress_by_chapter = {}

    def _session_progress_pct(sess):
        if sess.status == 'completed':
            return 100
        step = int(sess.progress_step or 0)
        # Try to get total from __plan__ in messages for precise %
        total = 0
        for m in (sess.messages or []):
            if m.get('role') == '__plan__':
                try:
                    import json as _j
                    tl = _j.loads(m.get('content', '{}'))
                    content = tl.get('content', '')
                    if content:
                        total = len(_j.loads(content))
                except Exception:
                    pass
                break
        if total > 0 and step >= 0:
            return min(100, round(step / total * 100))
        # Fallback: rough phase mapping
        return {
            0: 5,
            1: 15,
            2: 30,
            3: 50,
        }.get(step, min(95, step * 10))

    for sess in sessions:
        subj = (sess.chapter_subject or '').strip().lower()
        num = sess.chapter_num
        if not subj or num is None:
            continue
        key = (subj, int(num))
        pct = _session_progress_pct(sess)
        progress_by_chapter[key] = max(progress_by_chapter.get(key, 0), pct)

    chapters_by_subject = {}
    for subj, info in MATS.items():
        if subj not in user_subjs:
            continue
        chapters = _get_cours_chapters(subj)
        for ch in chapters:
            ch_num = int(ch.get('num', 0) or 0)
            ch['progress_pct'] = progress_by_chapter.get((subj, ch_num), 0)
        chapters_by_subject[subj] = {
            'info': info,
            'chapters': chapters,
            'count': len(chapters),
        }

    # Sessions récentes de l'utilisateur
    recent_sessions = CourseSession.objects.filter(
        user=request.user, status='active'
    ).order_by('-updated_at')[:5]

    return render(request, 'core/cours.html', {
        'profile': profile,
        'chapters_by_subject': chapters_by_subject,
        'recent_sessions': recent_sessions,
        'mats': MATS,
        'user_serie_subjects': list(user_subjs),
        'any_chapters': any(d['count'] > 0 for d in chapters_by_subject.values()),
    })


def sc_social_view(request):
    is_guest_user = _is_guest(request)
    if not is_guest_user and not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile = UserProfile.objects.get_or_create(user=request.user)[0] if request.user.is_authenticated else None
    raw_notes_path = Path(__file__).resolve().parent.parent / 'database' / 'note_sc_social.json'
    progress_state = {}
    try:
        raw_content = raw_notes_path.read_text(encoding='utf-8')
        if raw_content.lstrip().startswith('{'):
            sc_social_data = json.loads(raw_content)
        else:
            sc_social_data = {'sections': [], 'raw_text': raw_content}
    except (OSError, json.JSONDecodeError):
        sc_social_data = {'sections': [], 'raw_text': ''}

    if not is_guest_user and request.user.is_authenticated:
        progress = CourseProgressState.objects.filter(
            user=request.user,
            course_key=SC_SOCIAL_COURSE_KEY,
        ).first()
        if progress and isinstance(progress.state, dict):
            progress_state = progress.state

    return render(request, 'core/sc_social.html', {
        'profile': profile,
        'sc_social_data_json': json.dumps(sc_social_data, ensure_ascii=False),
        'sc_social_progress_json': json.dumps(progress_state, ensure_ascii=False),
        'is_guest': is_guest_user,
    })


def _clean_simple_course_progress_state(payload):
    if not isinstance(payload, dict):
        return {}

    def _clean_text(value, limit=200):
        if not isinstance(value, str):
            return ''
        return value[:limit]

    def _clean_int(value, default=0, min_value=0, max_value=None):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        if number < min_value:
            number = min_value
        if max_value is not None and number > max_value:
            number = max_value
        return number

    return {
        'version': _clean_int(payload.get('version'), default=1, min_value=1, max_value=10),
        'saved_at': _clean_int(payload.get('saved_at'), default=0, min_value=0),
        'scroll_top': _clean_int(payload.get('scroll_top'), default=0, min_value=0, max_value=10_000_000),
        'active_anchor': _clean_text(payload.get('active_anchor'), limit=160),
    }


def physique_view(request):
    is_guest_user = _is_guest(request)
    if not is_guest_user and not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile = UserProfile.objects.get_or_create(user=request.user)[0] if request.user.is_authenticated else None
    raw_notes_path = Path(__file__).resolve().parent.parent / 'database' / 'note_physique.json'
    progress_state = {}
    
    try:
        raw_content = raw_notes_path.read_text(encoding='utf-8')
        # Essayer de parser en JSON, sinon utiliser comme texte brut
        if raw_content.lstrip().startswith('{') or raw_content.lstrip().startswith('['):
            try:
                physique_data = json.loads(raw_content)
            except json.JSONDecodeError:
                physique_data = {'sections': [], 'raw_text': raw_content}
        else:
            physique_data = {'sections': [], 'raw_text': raw_content}
    except (OSError, json.JSONDecodeError):
        physique_data = {'sections': [], 'raw_text': ''}
    
    if not is_guest_user and request.user.is_authenticated:
        progress = CourseProgressState.objects.filter(
            user=request.user,
            course_key=PHYSIQUE_COURSE_KEY,
        ).first()
        if progress and isinstance(progress.state, dict):
            progress_state = progress.state

    return render(request, 'core/physique.html', {
        'profile': profile,
        'physique_course_json': json.dumps(physique_data, ensure_ascii=False),
        'physique_progress_json': json.dumps(progress_state, ensure_ascii=False),
        'is_guest': is_guest_user,
    })


def _load_generic_course_json(filename):
    """Charge un fichier JSON/texte depuis database/ et retourne {'raw_text': ...}"""
    path = Path(__file__).resolve().parent.parent / 'database' / filename
    try:
        raw = path.read_text(encoding='utf-8-sig')
        if raw.lstrip().startswith('{') or raw.lstrip().startswith('['):
            try:
                data = json.loads(raw)
                if 'raw_text' not in data:
                    data = {'raw_text': raw, 'sections': []}
                return data
            except json.JSONDecodeError:
                pass
        return {'raw_text': raw, 'sections': []}
    except OSError:
        return {'raw_text': '', 'sections': []}


def _convert_chapter_to_text(chap):
    """Convert a richly structured JSON chapter to readable plain text."""
    lines = []

    if chap.get('introduction'):
        lines.append(chap['introduction'])
        lines.append('')
    if chap.get('description'):
        lines.append(chap['description'])
        lines.append('')
    if chap.get('frequence_bac'):
        lines.append(f"📌 Fréquence BAC : {chap['frequence_bac']}")
        lines.append('')
    if chap.get('famille'):
        lines.append(f"**Famille :** {chap['famille']}")
        lines.append('')

    # --- concepts_cles (chimie style) ---
    for concept in chap.get('concepts_cles', []):
        lines.append(f"### {concept.get('nom', 'Concept')}")
        lines.append('')
        if concept.get('contenu'):
            lines.append(concept['contenu'])
            lines.append('')
        if concept.get('tableau_prefixes'):
            lines.append('**Tableau de nomenclature :**')
            for row in concept['tableau_prefixes']:
                n = row.get('n', ''); nom = row.get('nom', ''); pref = row.get('prefixe', '')
                lines.append(f"• n={n} → {nom} (préfixe : {pref})")
            lines.append('')
        if concept.get('regle'):
            lines.append(f"**Règle :** {concept['regle']}")
            lines.append('')
        if isinstance(concept.get('definitions'), dict):
            lines.append('**Définitions :**')
            for k, v in concept['definitions'].items():
                lines.append(f"• **{k.replace('_',' ').title()} :** {v}")
            lines.append('')
        if concept.get('exemples'):
            lines.append('**Exemples :**')
            for ex in concept['exemples'][:6]:
                if isinstance(ex, dict):
                    nom = ex.get('nom', '')
                    formula = ex.get('formule_brute', ex.get('formule', ex.get('formule_semi', '')))
                    M = ex.get('M', '')
                    txt = f"• {nom}"
                    if formula: txt += f" : {formula}"
                    if M: txt += f" — M = {M}"
                    lines.append(txt)
                elif isinstance(ex, str):
                    lines.append(f"• {ex}")
            lines.append('')
        for field in ('geometrie', 'liaisons'):
            if concept.get(field):
                label = 'Géométrie' if field == 'geometrie' else 'Liaisons'
                lines.append(f"**{label} :** {concept[field]}")
        if concept.get('definition'):
            lines.append(concept['definition'])
            lines.append('')
        if isinstance(concept.get('types'), list):
            lines.append('**Types :**')
            for t in concept['types']:
                lines.append(f"• {t}")
            lines.append('')
        if concept.get('astuce_bac'):
            lines.append(f"💡 **Astuce BAC :** {concept['astuce_bac']}")
            lines.append('')

    # --- methode (list or dict, tableau avancement style) ---
    meth = chap.get('methode')
    if meth and isinstance(meth, (list, dict)):
        lines.append('### Méthode')
        if isinstance(meth, list):
            for s in meth:
                lines.append(f"• {s}")
        else:
            for k, v in meth.items():
                if isinstance(v, str):
                    lines.append(f"• **{k.replace('_',' ').title()} :** {v}")
                elif isinstance(v, list):
                    lines.append(f"**{k.replace('_',' ').title()} :**")
                    for item in v:
                        lines.append(f"  • {item}")
        lines.append('')

    # --- methode_resolution / methode_calcul_volumes_masses ---
    for mkey in ('methode_resolution', 'methode_calcul_volumes_masses'):
        m = chap.get(mkey)
        if not m:
            continue
        label = 'Méthode de résolution' if mkey == 'methode_resolution' else 'Méthode de calcul'
        lines.append(f"### {label}")
        if isinstance(m, list):
            for s in m:
                lines.append(f"• {s}")
        elif isinstance(m, dict):
            for k, v in m.items():
                if isinstance(v, list):
                    lines.append(f"**{k.replace('_',' ').title()} :**")
                    for item in v:
                        lines.append(f"  • {item}")
                elif isinstance(v, str):
                    lines.append(f"• **{k.replace('_',' ').title()} :** {v}")
        lines.append('')

    # --- anglais: methode list ---
    if chap.get('methode') and isinstance(chap['methode'], list) and \
            any(isinstance(x, str) and x[0].isdigit() for x in chap.get('methode', [])):
        pass  # already handled above

    if chap.get('types_questions_frequentes'):
        lines.append('**Types de questions fréquentes :**')
        for q in chap['types_questions_frequentes']:
            lines.append(f"• {q}")
        lines.append('')
    if chap.get('textes_frequents_au_bac'):
        lines.append('**Thèmes fréquents au BAC :**')
        for theme in chap['textes_frequents_au_bac']:
            t = theme.get('theme', '')
            exs = ', '.join(theme.get('exemples', []))
            lines.append(f"• **{t} :** {exs}")
        lines.append('')

    # --- anglais: verb tenses ---
    for temps in chap.get('temps', []):
        lines.append(f"### {temps.get('nom', '')}")
        if temps.get('formation'):
            lines.append(f"**Formation :** {temps['formation']}")
        if temps.get('usage'):
            lines.append(f"**Usage :** {temps['usage']}")
        for ex in temps.get('exemples', []):
            lines.append(f"• {ex}")
        sw = temps.get('signal_words')
        if sw:
            if isinstance(sw, list): sw = ', '.join(sw)
            lines.append(f"**Signal words :** {sw}")
        lines.append('')

    # --- economie: explication_complete ---
    exp = chap.get('explication_complete', {})
    if exp:
        deb = exp.get('niveau_debutant', {})
        if deb.get('definition'):
            lines.append('**Pour comprendre :**')
            lines.append(deb['definition'])
            lines.append('')
        if deb.get('metaphore'):
            lines.append(f"💡 {deb['metaphore']}")
            lines.append('')
        mid = exp.get('niveau_intermediaire', {})
        if mid.get('formule'):
            lines.append(f"**Formule :** {mid['formule']}")
        if isinstance(mid.get('variables'), dict):
            lines.append('**Variables :**')
            for k, v in mid['variables'].items():
                lines.append(f"• **{k}** = {v}")
            lines.append('')
        if mid.get('revenu_disponible'):
            lines.append(f"**Revenu disponible :** {mid['revenu_disponible']}")
            lines.append('')
        avance = exp.get('niveau_avance', {})
        for k, v in avance.items():
            if isinstance(v, dict):
                lines.append(f"### {k.replace('_',' ').title()}")
                for sk, sv in v.items():
                    if isinstance(sv, str):
                        lines.append(f"• **{sk.replace('_',' ').title()} :** {sv}")
                lines.append('')

    # --- economie: exemples_concrets ---
    for ex in chap.get('exemples_concrets', []):
        if not isinstance(ex, dict):
            continue
        source = ex.get('source', '')
        enonce = ex.get('enonce', ex.get('probleme', ''))
        if source:
            lines.append(f"**{source} :**")
        if enonce:
            lines.append(enonce)
        res = ex.get('resolution', {})
        if isinstance(res, dict):
            for k, v in res.items():
                if isinstance(v, str):
                    lines.append(f"• {v}")
        lines.append('')

    # --- informatique: concepts ---
    for concept in chap.get('concepts', []):
        titre_c = concept.get('titre', str(concept.get('id', '')))
        lines.append(f"### {titre_c}")
        deb = concept.get('niveau_debutant', {})
        if deb.get('explication'):
            lines.append(deb['explication'])
        if deb.get('analogie'):
            lines.append(f"💡 {deb['analogie']}")
        avance = concept.get('niveau_avance', {})
        if avance.get('explication'):
            lines.append(avance['explication'])
        schema = concept.get('structure_complete', {})
        if schema.get('schema'):
            lines.append(f"\n{schema['schema']}\n")
        if isinstance(schema.get('mots_cles'), dict):
            lines.append('**Mots-clés :**')
            for k, v in schema['mots_cles'].items():
                lines.append(f"• **{k}** : {v}")
        for ex in concept.get('exemples_concrets', []):
            if isinstance(ex, dict) and ex.get('titre'):
                lines.append(f"**Exemple :** {ex['titre']}")
                if ex.get('code'):
                    lines.append(ex['code'])
        lines.append('')

    # --- art / generic / espagnol: sections array ---
    for section in chap.get('sections', []):
        titre_s = section.get('titre', '')
        if titre_s:
            lines.append(f"### {titre_s}")
            lines.append('')
        # introduction / explication
        for fld in ('introduction', 'explication', 'description'):
            val = section.get(fld, '')
            if val and isinstance(val, str):
                lines.append(val)
                lines.append('')
        # niveau_debutant (str or dict)
        deb = section.get('niveau_debutant', '')
        if isinstance(deb, str) and deb:
            lines.append(deb)
            lines.append('')
        elif isinstance(deb, dict):
            if deb.get('explication'): lines.append(deb['explication']); lines.append('')
            if deb.get('analogie'): lines.append(f"💡 {deb['analogie']}"); lines.append('')
        # niveau_avance (str or dict)
        avance = section.get('niveau_avance', '')
        if isinstance(avance, str) and avance:
            lines.append(avance); lines.append('')
        elif isinstance(avance, dict):
            if avance.get('explication'): lines.append(avance['explication']); lines.append('')
        # formation (dict with tableau sub-keys)
        formation = section.get('formation')
        if isinstance(formation, dict):
            lines.append('**Formation :**')
            for k, v in formation.items():
                if isinstance(v, str):
                    lines.append(f"• **{k.replace('_',' ')}** : {v}")
                elif isinstance(v, dict):
                    if v.get('tableau'):
                        lines.append(f"**{k.replace('_',' ').title()} :**")
                        for subj_key, form in v['tableau'].items():
                            lines.append(f"  • {subj_key} → {form}")
                    elif v.get('infinitif_exemple'):
                        lines.append(f"  {v.get('infinitif_exemple','')}")
                        for sk2, sv2 in v.items():
                            if sk2 != 'infinitif_exemple' and isinstance(sv2, str):
                                lines.append(f"  {sk2} → {sv2}")
                    else:
                        for sk, sv in v.items():
                            if isinstance(sv, str): lines.append(f"  • {sk} : {sv}")
            lines.append('')
        # formation_affirmatif / formation_negatif
        for fld2 in ('formation_affirmatif', 'formation_negatif'):
            fm2 = section.get(fld2)
            if fm2 and isinstance(fm2, dict):
                lines.append(f"**{fld2.replace('_',' ').title()} :**")
                for k,v in fm2.items():
                    if isinstance(v, str): lines.append(f"  • {k} : {v}")
                lines.append('')
        # verbes_consignes (dict name->definition)
        vc = section.get('verbes_consignes')
        if isinstance(vc, dict):
            lines.append('**Verbes de consignes :**')
            for vname, vdef in vc.items():
                lines.append(f"• **{vname}** : {vdef}")
            lines.append('')
        # quand_utiliser
        qu = section.get('quand_utiliser')
        if isinstance(qu, list):
            lines.append('**Quand utiliser :**')
            for item in qu: lines.append(f"• {item}")
            lines.append('')
        elif isinstance(qu, str) and qu:
            lines.append(f"**Quand utiliser :** {qu}"); lines.append('')
        # methode_reponse / methode / methode_en_4_etapes
        for fld3 in ('methode_reponse', 'methode', 'methode_en_4_etapes', 'methode_exercice'):
            m3 = section.get(fld3)
            if isinstance(m3, dict):
                lines.append(f"**{fld3.replace('_',' ').title()} :**")
                for step, desc in m3.items():
                    lines.append(f"• **{step}** : {desc}")
                lines.append('')
            elif isinstance(m3, list):
                lines.append(f"**{fld3.replace('_',' ').title()} :**")
                for item in m3: lines.append(f"• {item}")
                lines.append('')
        # irreguliers_importants
        irr = section.get('irreguliers_importants')
        if isinstance(irr, dict):
            lines.append('**Verbes irréguliers importants :**')
            for vb, conj in irr.items():
                lines.append(f"• **{vb}** : {conj}")
            lines.append('')
        # indefinido_vs_imperfecto
        cmp = section.get('indefinido_vs_imperfecto')
        if isinstance(cmp, dict):
            lines.append('**Comparaison Indéfini vs Imparfait :**')
            for k, v in cmp.items():
                if isinstance(v, dict):
                    lines.append(f"**{k} :**")
                    for sk, sv in v.items():
                        lines.append(f"  • {sk} : {sv}")
                else:
                    lines.append(f"• {k} : {v}")
            lines.append('')
        # ser_utilisations / estar_utilisations
        for fld4 in ('ser_utilisations', 'estar_utilisations'):
            su = section.get(fld4)
            if isinstance(su, list):
                lines.append(f"**{fld4.replace('_',' ').title()} :**")
                for item in su: lines.append(f"• {item}")
                lines.append('')
        # pronoms_COD / pronoms_COI
        for fld5 in ('pronoms_COD', 'pronoms_COI'):
            pr = section.get(fld5)
            if isinstance(pr, dict):
                lines.append(f"**{fld5} :**")
                for k, v in pr.items(): lines.append(f"• {k} → {v}")
                lines.append('')
        # connecteurs_logiques
        cl = section.get('connecteurs_logiques')
        if isinstance(cl, dict):
            lines.append('**Connecteurs logiques :**')
            for cat, items in cl.items():
                lines.append(f"**{cat} :**")
                if isinstance(items, list):
                    for item in items: lines.append(f"  • {item}")
                elif isinstance(items, str):
                    lines.append(f"  {items}")
            lines.append('')
        # themes
        themes = section.get('themes')
        if isinstance(themes, list):
            lines.append('**Thèmes :**')
            for t in themes: lines.append(f"• {t}")
            lines.append('')
        elif isinstance(themes, dict):
            for cat, items in themes.items():
                lines.append(f"**{cat} :**")
                if isinstance(items, list):
                    for item in items: lines.append(f"  • {item}")
            lines.append('')
        # structure / structure_lettre_formelle / structure_cle
        for fld6 in ('structure', 'structure_cle', 'structure_lettre_formelle'):
            st = section.get(fld6)
            if isinstance(st, dict):
                lines.append(f"**{fld6.replace('_',' ').title()} :**")
                for k, v in st.items():
                    if isinstance(v, str): lines.append(f"• **{k}** : {v}")
                    elif isinstance(v, list): lines.append(f"• **{k}** : {', '.join(str(x) for x in v)}")
                lines.append('')
            elif isinstance(st, list):
                lines.append(f"**{fld6.replace('_',' ').title()} :**")
                for item in st: lines.append(f"• {item}")
                lines.append('')
        # type_question_bac / type_question_bac_1 / type_question_bac_2
        for fld7 in ('type_question_bac', 'type_question_bac_1', 'type_question_bac_2', 'type_question_bac_idiomes'):
            tqb = section.get(fld7)
            if tqb and isinstance(tqb, str):
                lines.append(f"**Type question BAC :** {tqb}"); lines.append('')
        # exemples_bac / exemples_vrais_examens / exemple / exemple_complet
        for fld8 in ('exemples_bac', 'exemples_vrais_examens', 'exemple', 'exemple_complet',
                     'exemple_redaction_complete', 'exemple_dialogue_bac', 'exemple_lettre_bac'):
            ex_val = section.get(fld8)
            if ex_val and isinstance(ex_val, str):
                lines.append(f"**Exemple :** {ex_val}"); lines.append('')
            elif isinstance(ex_val, list):
                lines.append('**Exemples :**')
                for e in ex_val:
                    if isinstance(e, str): lines.append(f"• {e}")
                    elif isinstance(e, dict):
                        for k, v in e.items():
                            if isinstance(v, str): lines.append(f"• **{k}** : {v}")
                lines.append('')
            elif isinstance(ex_val, dict):
                lines.append(f"**{fld8.replace('_',' ').title()} :**")
                for k, v in ex_val.items():
                    if isinstance(v, str): lines.append(f"• **{k}** : {v}")
                    elif isinstance(v, list): lines.append(f"• **{k}** : {', '.join(str(x) for x in v[:5])}")
                lines.append('')
        # expressions_conditionnelles / expressions_utiles_pour_resumer / expressions_utiles_lettre / formules_de_base
        for fld9 in ('expressions_conditionnelles', 'expressions_utiles_pour_resumer', 'expressions_utiles_lettre', 'formules_de_base'):
            ev = section.get(fld9)
            if isinstance(ev, list):
                lines.append(f"**{fld9.replace('_',' ').title()} :**")
                for item in ev: lines.append(f"• {item}")
                lines.append('')
        # faits_cles / astuces_bac
        faits = section.get('faits_cles', [])
        if faits:
            lines.append('**Faits clés :**')
            for f in faits: lines.append(f"• {f}")
            lines.append('')
        astuces = section.get('astuces_bac', [])
        if astuces:
            lines.append('**Astuces BAC :**')
            for a in astuces: lines.append(f"💡 {a}")
            lines.append('')
        # conseils
        conseils = section.get('conseils')
        if isinstance(conseils, list):
            lines.append('**Conseils :**')
            for c in conseils: lines.append(f"• {c}")
            lines.append('')
        # exemples_systemes (informatique OS)
        exemples_sys = section.get('exemples_systemes', {})
        if isinstance(exemples_sys, dict):
            for name, info in exemples_sys.items():
                if isinstance(info, dict) and info.get('description'):
                    lines.append(f"• **{name}** : {info['description']}")
            if exemples_sys: lines.append('')
        # pieges / pieges_frequents inside section
        for pflabel in ('pieges', 'pieges_frequents'):
            sp = section.get(pflabel, [])
            if isinstance(sp, list) and sp:
                lines.append('⚠️ **Pièges :**')
                for p in sp: lines.append(f"• {p}")
                lines.append('')
            elif isinstance(sp, str) and sp:
                lines.append(f"⚠️ **Piège :** {sp}"); lines.append('')
        # position (pronoms order)
        pos = section.get('position')
        if isinstance(pos, dict):
            lines.append('**Position :**')
            for k, v in pos.items():
                if isinstance(v, str): lines.append(f"• **{k}** : {v}")
            lines.append('')
        # mots_interrogatifs / champs_frequents
        for fld10 in ('mots_interrogatifs', 'champs_frequents'):
            val10 = section.get(fld10)
            if isinstance(val10, dict):
                lines.append(f"**{fld10.replace('_',' ').title()} :**")
                for k, v in val10.items():
                    lines.append(f"• **{k}** : {v}")
                lines.append('')
        # pronoms_avec_imperatif
        pai = section.get('pronoms_avec_imperatif')
        if isinstance(pai, dict):
            lines.append('**Pronoms avec impératif :**')
            for k, v in pai.items():
                lines.append(f"• {k} : {v}")
            lines.append('')
        # transformation_type_bac
        ttb = section.get('transformation_type_bac')
        if isinstance(ttb, dict):
            lines.append('**Transformation type BAC :**')
            for k, v in ttb.items():
                if isinstance(v, str): lines.append(f"• {k} : {v}")
            lines.append('')

    # --- Pièges ---
    pieges = chap.get('pieges_frequents', chap.get('piege_majeur'))
    if pieges:
        lines.append('### ⚠️ Pièges fréquents')
        if isinstance(pieges, list):
            for p in pieges:
                lines.append(f"• {p}")
        elif isinstance(pieges, str):
            lines.append(f"• {pieges}")
        lines.append('')

    return '\n'.join(lines)


def _fix_latex_in_text(text: str) -> str:
    """Fix broken LaTeX patterns in course text so KaTeX renders correctly."""
    import re as _re
    if not text:
        return text
    # Fix: $\$mu_{0}$ → $\mu_{0}$  (double-dollar with backslash-dollar)
    text = _re.sub(r'\$\\?\$(\\[a-zA-Z_{}^0-9 ]+)\$', r'$\1$', text)
    # Fix: |e| = L \times \frac{|$I_{2}$ - $I_{1}$|}{\Delta t} → wrap in proper $...$
    # Ensure bare LaTeX commands outside $ are wrapped
    # Fix patterns like: B = \$mu_{0}$ → B = $\mu_{0}$
    text = _re.sub(r'\\?\$\\([a-zA-Z]+)(\{[^}]*\})?\$', r'$\\\1\2$', text)
    # Remove stray backslash-dollar: \$ → (nothing, or keep as is for currency)
    # Only remove \$ when it appears to be a failed escape inside math context
    text = _re.sub(r'\\\$([a-zA-Z])', r'$\1', text)
    return text


def _rich_json_to_course_text(data):
    """Convert a richly structured course JSON to the CHAPITRE N — TITLE\ncontent format."""
    chapitres = data.get('chapitres') or data.get('chapters', [])
    if not chapitres:
        return data.get('raw_text', '')
    lines = []
    for chap in chapitres:
        idx = chap.get('id', len(lines) + 1)
        titre = chap.get('titre', chap.get('title', f'Chapitre {idx}'))
        lines.append(f"CHAPITRE {idx} — {titre}\n")
        chapter_text = _convert_chapter_to_text(chap)
        lines.append(_fix_latex_in_text(chapter_text))
        lines.append('\n')
    return '\n'.join(lines)


def math_cours_view(request):
    is_guest_user = _is_guest(request)
    if not is_guest_user and not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile = UserProfile.objects.get_or_create(user=request.user)[0] if request.user.is_authenticated else None
    data = _load_generic_course_json('note_math.json')
    return render(request, 'core/generic_cours.html', {
        'profile': profile,
        'course_json': json.dumps(data, ensure_ascii=False),
        'course_name': 'Math',
        'course_icon': 'fas fa-calculator',
        'course_color_1': '#34d399',
        'course_color_2': '#10b981',
        'course_color_3': '#6ee7b7',
        'course_title': 'Maîtrise les Mathématiques pour le Baccalauréat Haïtien',
        'course_desc': 'Fonctions, suites, complexes, probabilités, statistiques, intégrales et géométrie — tous les chapitres essentiels.',
        'is_guest': is_guest_user,
    })


def svt_cours_view(request):
    is_guest_user = _is_guest(request)
    if not is_guest_user and not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile = UserProfile.objects.get_or_create(user=request.user)[0] if request.user.is_authenticated else None
    data = _load_generic_course_json('note_SVT.json')
    return render(request, 'core/generic_cours.html', {
        'profile': profile,
        'course_json': json.dumps(data, ensure_ascii=False),
        'course_name': 'SVT',
        'course_icon': 'fas fa-leaf',
        'course_color_1': '#4ade80',
        'course_color_2': '#22c55e',
        'course_color_3': '#86efac',
        'course_title': 'Maîtrise la SVT pour le Baccalauréat Haïtien',
        'course_desc': 'Génétique, évolution, écologie, géologie — Sciences de la Vie et de la Terre complètes.',
        'is_guest': is_guest_user,
    })


def kreyol_cours_view(request):
    is_guest_user = _is_guest(request)
    if not is_guest_user and not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile = UserProfile.objects.get_or_create(user=request.user)[0] if request.user.is_authenticated else None
    data = _load_generic_course_json('note_kreyol.json')
    return render(request, 'core/generic_cours.html', {
        'profile': profile,
        'course_json': json.dumps(data, ensure_ascii=False),
        'course_name': 'Kreyòl',
        'course_icon': 'fas fa-language',
        'course_color_1': '#f472b6',
        'course_color_2': '#ec4899',
        'course_color_3': '#fb7185',
        'course_title': 'Maîtrise le Kreyòl Ayisyen pou Bak la',
        'course_desc': 'Konpreyansyon tèks, gramè, pwoduksyon ekri, ak analiz literè — tout sa ou bezwen pou reyisi.',
        'is_guest': is_guest_user,
    })


def chimie_cours_view(request):
    is_guest_user = _is_guest(request)
    if not is_guest_user and not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile = UserProfile.objects.get_or_create(user=request.user)[0] if request.user.is_authenticated else None
    path = Path(__file__).resolve().parent.parent / 'database' / 'note_de_Chimie.json'
    try:
        raw_json = json.loads(path.read_text(encoding='utf-8-sig'))
        course_text = _rich_json_to_course_text(raw_json)
    except Exception:
        course_text = ''
    data = {'raw_text': course_text}
    return render(request, 'core/generic_cours.html', {
        'profile': profile,
        'course_json': json.dumps(data, ensure_ascii=False),
        'course_name': 'Chimie',
        'course_icon': 'fas fa-flask',
        'course_color_1': '#a78bfa',
        'course_color_2': '#7c3aed',
        'course_color_3': '#c4b5fd',
        'course_title': 'Maîtrise la Chimie pour le Baccalauréat Haïtien',
        'course_desc': 'Hydrocarbures, alcools, acides-bases, oxydoréduction — cours complet du programme BAC Haïti.',
        'is_guest': is_guest_user,
    })




def _make_rich_json_view(filename, course_name, icon, c1, c2, c3, title, desc):
    """Factory: returns a view that loads filename and renders generic_cours.html (guest-aware)."""
    def _view(request):
        is_guest_user = _is_guest(request)
        if not is_guest_user and not request.user.is_authenticated:
            return redirect('/login/?next=' + request.get_full_path())
        profile = None
        if request.user.is_authenticated:
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
        fpath = Path(__file__).resolve().parent.parent / 'database' / filename
        try:
            # Pass the complete JSON structure directly for universal extraction in frontend
            data = json.loads(fpath.read_text(encoding='utf-8-sig'))
        except Exception:
            data = {}
        return render(request, 'core/generic_cours.html', {
            'profile': profile,
            'course_json': json.dumps(data, ensure_ascii=False),
            'course_name': course_name,
            'course_icon': icon,
            'course_color_1': c1,
            'course_color_2': c2,
            'course_color_3': c3,
            'course_title': title,
            'course_desc': desc,
            'is_guest': is_guest_user,
        })
    return _view


anglais_cours_view = _make_rich_json_view(
    'note_anglais.json', 'Anglais', 'fas fa-flag',
    '#38bdf8', '#0284c7', '#7dd3fc',
    "Maîtrise l'Anglais pour le Baccalauréat Haïtien",
    'Reading comprehension, grammaire, expression écrite et orale — toutes les compétences du BAC.',
)

economie_cours_view = _make_rich_json_view(
    'note_economie.json', 'Économie', 'fas fa-chart-bar',
    '#fbbf24', '#d97706', '#fde68a',
    "Maîtrise l'Économie pour le Baccalauréat Haïtien",
    'Fonction de consommation, PIB, monnaie, politique économique — cours complet Éco BAC Haïti.',
)

histoire_cours_view = _make_rich_json_view(
    'note_sc_social.json', 'Sciences Sociales', 'fas fa-landmark',
    '#fb923c', '#ea580c', '#fed7aa',
    "Maîtrise les Sciences Sociales pour le Baccalauréat Haïtien",
    'Histoire nationale haïtienne, histoire universelle, géographie économique — cours BAC Haïti.',
)

physique_view = _make_rich_json_view(
    'note_physique.json', 'Physique', 'fas fa-atom',
    '#8b5cf6', '#6d28d9', '#c4b5fd',
    'Maîtrise la Physique pour le Baccalauréat Haïtien',
    'Condensateurs, électrostatique, associations et méthodes BAC — cours complet avec assistant IA.',
)

# Keep legacy route name/URL while serving the true JSON-backed social-science course page.
sc_social_view = histoire_cours_view


informatique_cours_view = _make_rich_json_view(
    'note_informatique.json', 'Informatique', 'fas fa-laptop-code',
    '#34d399', '#059669', '#6ee7b7',
    "Maîtrise l'Informatique pour le Baccalauréat Haïtien",
    'Algorithmique, réseaux, HTML, architecture — tout le programme Informatique BAC Haïti.',
)

art_cours_view = _make_rich_json_view(
    'note_art.json', 'Art & Musique', 'fas fa-palette',
    '#e879f9', '#a21caf', '#f0abfc',
    "Maîtrise l'Art & Musique pour le Baccalauréat Haïtien",
    "Histoire de l'art haïtien, arts plastiques, musique — série LLA.",
)

espagnol_cours_view = _make_rich_json_view(
    'note_espagnol.json', 'Espagnol', 'fas fa-language',
    '#f97316', '#c2410c', '#fed7aa',
    "Maîtrise l'Espagnol pour le Baccalauréat Haïtien",
    'Compréhension, grammaire et expression — toutes les compétences Espagnol BAC Haïti.',
)


def philosophie_cours_view(request):
    is_guest_user = _is_guest(request)
    if not is_guest_user and not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile = UserProfile.objects.get_or_create(user=request.user)[0] if request.user.is_authenticated else None
    data = _load_generic_course_json('note_philosophie.json')
    return render(request, 'core/generic_cours.html', {
        'profile': profile,
        'course_json': json.dumps(data, ensure_ascii=False),
        'course_name': 'Philosophie',
        'course_icon': 'fas fa-brain',
        'course_color_1': '#e879f9',
        'course_color_2': '#a21caf',
        'course_color_3': '#f0abfc',
        'course_title': 'Maîtrise la Philosophie pour le Baccalauréat Haïtien',
        'course_desc': 'Dissertation, étude de texte, grands concepts (liberté, morale, nature/culture) et auteurs incontournables — cours complet Philo BAC Haïti.',
        'is_guest': is_guest_user,
    })


@login_required
def physique_exercises_view(request, section_id):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    physique_course = _get_physique_course_data()
    section = physique_course.get('section_map', {}).get(section_id)
    if not section:
        return redirect('physique_course')

    outline = physique_course.get('outline', [])
    exercise_bank = _get_or_generate_physique_exercise_bank(section)
    initial_exercises = exercise_bank
    initial_next_offset = len(initial_exercises)

    return render(request, 'core/physique_exercises.html', {
        'profile': profile,
        'physique_outline_json': json.dumps(outline, ensure_ascii=False),
        'physique_exercise_detail_base': reverse('physique_exercise_detail_page', args=[section['id'], 0]).replace('/0/', '/__INDEX__/'),
        'physique_current_section_json': json.dumps({
            'id': section['id'],
            'title': section['title'],
            'category': section['category'],
            'summary': section['summary'],
        }, ensure_ascii=False),
        'physique_initial_exercises_json': json.dumps(initial_exercises, ensure_ascii=False),
        'physique_initial_next_offset': initial_next_offset,
    })


@login_required
def physique_exercise_detail_view(request, section_id, exercise_index):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    physique_course = _get_physique_course_data()
    section = physique_course.get('section_map', {}).get(section_id)
    if not section:
        return redirect('physique_course')

    outline = physique_course.get('outline', [])
    exercise_bank = _get_or_generate_physique_exercise_bank(section)
    if not exercise_bank:
        return redirect('physique_exercises_page', section_id=section_id)

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[physique_exercise_detail_view] section_id={section_id}, exercise_index={exercise_index}, len(exercise_bank)={len(exercise_bank)}")
    try:
        selected_exercise = exercise_bank[int(exercise_index)]
    except (ValueError, TypeError):
        return redirect('physique_exercises_page', section_id=section_id)
    except IndexError:
        if not exercise_bank:
            return redirect('physique_exercises_page', section_id=section_id)
        # Si on demande un index hors limites (peut arriver juste après génération), on affiche le dernier exercice existant.
        logger.warning(f"[physique_exercise_detail_view] IndexError: exercise_index={exercise_index} >= len={len(exercise_bank)}, using last")
        exercise_index = len(exercise_bank) - 1
        selected_exercise = exercise_bank[-1]

    return render(request, 'core/physique_exercise_detail.html', {
        'profile': profile,
        'physique_outline_json': json.dumps(outline, ensure_ascii=False),
        'physique_current_section_json': json.dumps({
            'id': section['id'],
            'title': section['title'],
            'category': section['category'],
            'summary': section['summary'],
        }, ensure_ascii=False),
        'physique_exercise_json': json.dumps(selected_exercise, ensure_ascii=False),
        'physique_exercise_index': int(exercise_index),
        'physique_total_exercises': len(exercise_bank),
        'physique_exercises_page_url': reverse('physique_exercises_page', args=[section['id']]),
        'physique_exercise_detail_base': reverse('physique_exercise_detail_page', args=[section['id'], 0]).replace('/0/', '/__INDEX__/'),
        'physique_generate_similar_url': reverse('physique_exercise_similar_page', args=[section['id'], int(exercise_index)]),
    })


@login_required
@require_POST
def physique_exercise_similar_view(request, section_id, exercise_index):
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[physique_exercise_similar_view] ≡ START: section={section_id}, current_index={exercise_index}")
    
    physique_course = _get_physique_course_data()
    section = physique_course.get('section_map', {}).get(section_id)
    if not section:
        logger.error(f"[physique_exercise_similar_view] Section not found: {section_id}")
        return JsonResponse({'success': False, 'error': 'Section non trouvée'}, status=404)

    try:
        exercise_bank = _get_or_generate_physique_exercise_bank(section)
        if not exercise_bank:
            logger.error(f"[physique_exercise_similar_view] No exercise bank found")
            return JsonResponse({'success': False, 'error': 'Pas d\'exercices dans la banque'}, status=500)

        logger.info(f"[physique_exercise_similar_view] Exercise bank size: {len(exercise_bank)}")

        # Phase 1: Try AI generation without fallback
        logger.info(f"[physique_exercise_similar_view] Phase 1: Trying AI generation (no fallback)")
        generated_items = _generate_more_physique_exercises(section, exercise_bank, count=1, allow_fallbacks=False)
        
        if isinstance(generated_items, list) and generated_items:
            logger.info(f"[physique_exercise_similar_view] ✓ Phase 1 SUCCESS: {len(generated_items)} exercises generated")
            new_index = len(exercise_bank)
            merged_bank = _append_generated_physique_exercises(section, generated_items)
            exercise = merged_bank[new_index] if new_index < len(merged_bank) else merged_bank[-1]
        else:
            # Phase 2: Try fallback generation
            logger.warning(f"[physique_exercise_similar_view] Phase 1 failed. Phase 2: Trying with fallback")
            generated_items = _generate_more_physique_exercises(section, exercise_bank, count=1, allow_fallbacks=True)
            
            if isinstance(generated_items, list) and generated_items:
                logger.info(f"[physique_exercise_similar_view] ✓ Phase 2 SUCCESS: {len(generated_items)} fallback exercises")
                new_index = len(exercise_bank)
                merged_bank = _append_generated_physique_exercises(section, generated_items)
                exercise = merged_bank[new_index] if new_index < len(merged_bank) else merged_bank[-1]
            else:
                # Phase 3: Manual fallback
                logger.error(f"[physique_exercise_similar_view] Phase 1 AND 2 failed. Phase 3: Creating manual fallback")
                simple_exercise = {
                    'title': f'Exercice {len(exercise_bank) + 1} — {section["title"]}',
                    'theme': section['title'],
                    'intro': f'Exercice supplementaire sur {section["title"]}. Applique les concepts vus.',
                    'enonce': f'Exercice supplementaire sur {section["title"]}. Applique les concepts vus.',
                    'questions': ['Resous cette question en utilisant les formules apprises.'],
                    'hints': ['Utilise les formules du chapitre et applique etape par etape.'],
                    'solution': 'Solution a calculer selon les donnees.',
                    'conseils': 'Relis le cours et pratique.',
                    'source': 'Exercice similaire IA',
                    'difficulte': 'moyen',
                }
                normalized = _normalize_physique_exercise(simple_exercise, len(exercise_bank))
                merged_bank = _append_generated_physique_exercises(section, [normalized])
                exercise = merged_bank[-1]
                logger.info(f"[physique_exercise_similar_view] ✓ Phase 3 created manual fallback")

        logger.info(f"[physique_exercise_similar_view] ≡ SUCCESS: Returning exercise '{exercise.get('title')}'")
        return JsonResponse({
            'success': True,
            'exercise': exercise,
        }, status=200)
    except Exception as e:
        logger.error(f"[physique_exercise_similar_view] ≡ EXCEPTION: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Erreur interne du serveur.',
        }, status=500)


@login_required
@require_POST
def api_physique_section(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide.'}, status=400)

    section_id = str(body.get('section_id', '')).strip()
    mode = body.get('mode', 'normal')
    weak_points = _clean_weak_points(body.get('weak_points'))
    if mode not in {'normal', 'remediation'}:
        mode = 'normal'

    physique_course = _get_physique_course_data()
    section = physique_course.get('section_map', {}).get(section_id)
    if not section:
        return JsonResponse({'error': 'Section introuvable.'}, status=404)

    content = _get_or_generate_physique_lesson(section, mode=mode, weak_points=weak_points)
    return JsonResponse({
        'section_id': section['id'],
        'section_title': section['title'],
        'content': content,
        'mode': mode,
        'summary': section['summary'],
        'from_cache': mode == 'normal' and bool(_get_cached_generated_asset(section['id'], 'lesson', mode='normal')),
    })


@login_required
@require_POST
def api_physique_miniquiz(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide.'}, status=400)

    section_id = str(body.get('section_id', '')).strip()
    mode = body.get('mode', 'normal')
    weak_points = _clean_weak_points(body.get('weak_points'))
    if mode not in {'normal', 'remediation'}:
        mode = 'normal'

    physique_course = _get_physique_course_data()
    section = physique_course.get('section_map', {}).get(section_id)
    if not section:
        return JsonResponse({'error': 'Section introuvable.'}, status=404)

    questions = _get_or_generate_physique_quiz(section, mode=mode, weak_points=weak_points)
    return JsonResponse({
        'section_id': section['id'],
        'section_title': section['title'],
        'questions': questions,
        'mode': mode,
        'from_cache': mode == 'normal' and bool(_get_cached_generated_asset(section['id'], 'quiz', mode='normal')),
    })


@login_required
@require_POST
def api_physique_exercises(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide.'}, status=400)

    section_id = str(body.get('section_id', '')).strip()
    offset = body.get('offset', 0)
    limit = body.get('limit', 2)
    force_ai = bool(body.get('force_ai'))
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(4, int(limit)))
    except (TypeError, ValueError):
        limit = 2

    physique_course = _get_physique_course_data()
    section = physique_course.get('section_map', {}).get(section_id)
    if not section:
        return JsonResponse({'error': 'Section introuvable.'}, status=404)

    bank = _get_or_generate_physique_exercise_bank(section)
    selected = []
    source = 'stock'
    exhausted = False
    route_start_index = offset

    if force_ai:
        generated_items = _generate_more_physique_exercises(section, bank or [], count=limit, allow_fallbacks=False)
        route_start_index = len(bank)
        merged_bank = _append_generated_physique_exercises(section, generated_items)
        selected = merged_bank[route_start_index:route_start_index + len(generated_items)]
        source = 'generated'
        exhausted = True
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[api_physique_exercises] force_ai: generated {len(generated_items)}, route_start_index={route_start_index}, len(merged_bank)={len(merged_bank)}")
    else:
        selected = bank[offset:offset + limit]
        exhausted = offset + len(selected) >= len(bank)
        if not selected:
            generated_items = _generate_more_physique_exercises(section, bank or [], count=limit)
            route_start_index = len(bank)
            merged_bank = _append_generated_physique_exercises(section, generated_items)
            selected = merged_bank[route_start_index:route_start_index + len(generated_items)]
            source = 'generated'
            exhausted = True

    if source == 'stock':
        route_start_index = offset

    return JsonResponse({
        'section_id': section['id'],
        'section_title': section['title'],
        'exercises': selected,
        'source': source,
        'forced_ai': force_ai,
        'route_start_index': route_start_index,
        'next_offset': offset + len(selected) if source == 'stock' else offset,
        'stock_size': len(_get_or_generate_physique_exercise_bank(section)),
        'stock_exhausted': exhausted,
    })


@login_required
@require_POST
def api_physique_progress(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'JSON invalide.'}, status=400)

    state = _clean_simple_course_progress_state(body.get('state'))
    progress, _ = CourseProgressState.objects.update_or_create(
        user=request.user,
        course_key=PHYSIQUE_COURSE_KEY,
        defaults={'state': state},
    )
    return JsonResponse({'ok': True, 'updated_at': progress.updated_at.isoformat()})


def _clean_sc_social_progress_state(payload):
    if not isinstance(payload, dict):
        return {}

    def _clean_text(value, limit=12000):
        if not isinstance(value, str):
            return ''
        return value[:limit]

    def _clean_bool(value):
        return bool(value)

    def _clean_int(value, default=0, min_value=0, max_value=None):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        if number < min_value:
            number = min_value
        if max_value is not None and number > max_value:
            number = max_value
        return number

    cleaned = {
        'version': _clean_int(payload.get('version'), default=1, min_value=1, max_value=10),
        'saved_at': _clean_int(payload.get('saved_at'), default=0, min_value=0),
        'scroll_top': _clean_int(payload.get('scroll_top'), default=0, min_value=0, max_value=10_000_000),
        'active_chapter_id': _clean_text(payload.get('active_chapter_id'), limit=120),
        'chapter_states': {},
    }

    chapter_states = payload.get('chapter_states')
    if not isinstance(chapter_states, dict):
        return cleaned

    for chapter_id, chapter_state in chapter_states.items():
        chapter_key = _clean_text(chapter_id, limit=120)
        if not chapter_key or not isinstance(chapter_state, dict):
            continue

        quiz_selections = []
        for selection in chapter_state.get('quiz_selections') or []:
            if selection is None:
                quiz_selections.append(None)
                continue
            try:
                quiz_selections.append(int(selection))
            except (TypeError, ValueError):
                quiz_selections.append(None)

        essay_drafts = [
            _clean_text(item, limit=20000)
            for item in (chapter_state.get('essay_drafts') or [])
        ]

        essay_feedbacks = []
        for feedback in chapter_state.get('essay_feedbacks') or []:
            if not isinstance(feedback, dict):
                essay_feedbacks.append({'text': '', 'visible': False})
                continue
            essay_feedbacks.append({
                'text': _clean_text(feedback.get('text'), limit=20000),
                'visible': _clean_bool(feedback.get('visible')),
            })

        cleaned['chapter_states'][chapter_key] = {
            'quiz_selections': quiz_selections,
            'quiz_validated': _clean_bool(chapter_state.get('quiz_validated')),
            'essay_drafts': essay_drafts,
            'essay_feedbacks': essay_feedbacks,
        }

    return cleaned


@login_required
@require_POST
def api_sc_social_progress(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'JSON invalide.'}, status=400)

    state = _clean_sc_social_progress_state(body.get('state'))
    progress, _ = CourseProgressState.objects.update_or_create(
        user=request.user,
        course_key=SC_SOCIAL_COURSE_KEY,
        defaults={'state': state},
    )
    return JsonResponse({
        'ok': True,
        'updated_at': progress.updated_at.isoformat(),
    })


@login_required
@require_POST
def api_sc_social_correct(request):
    try:
        body = json.loads(request.body)
        answer = (body.get('answer') or '').strip()
        question = (body.get('question') or '').strip()
        chapter = (body.get('chapter') or '').strip()
        focus_points = body.get('focus_points') or []

        if not answer or not question:
            return JsonResponse({'ok': False, 'error': 'Question ou reponse manquante.'}, status=400)

        focus_text = '\n'.join(f'- {item}' for item in focus_points if item)
        prompt = (
            'Tu es un correcteur expert du Bac haitien en Sciences Sociales. '\
            'Tu corriges une reponse redigee en francais. '\
            'Donne un retour utile, exigeant, mais clair pour un eleve.\n\n'
            f'Chapitre: {chapter}\n'
            f'Question: {question}\n'
            'Points attendus:\n'
            f'{focus_text or "- Aucun point guide fourni"}\n\n'
            'Reponse de l eleve:\n'
            f'{answer}\n\n'
            'Reponds strictement avec ce plan en texte brut:\n'
            'Note estimee /20: ...\n'
            'Forces: ...\n'
            'Manques: ...\n'
            'Conseil de progression: ...\n'
            'Mini reponse modele: ...'
        )

        feedback = gemini._call(
            prompt,
            system='Tu es un correcteur de Sciences Sociales centre sur l histoire haitienne et universelle. Sois precis, factuel et pedagogique.',
            max_tokens=900,
        ).strip()

        stats = _get_or_create_stats(request.user)
        stats.minutes_etude += 6
        stats.save(update_fields=['minutes_etude'])

        return JsonResponse({'ok': True, 'feedback': feedback})
    except Exception as e:
        _logger.exception('Server error')
        return JsonResponse({'ok': False, 'error': 'Erreur interne du serveur.'}, status=500)


def chapter_cours_view(request, subject, num):
    """Page du cours interactif — chapitre depuis le JSON programme."""
    if _is_guest(request):
        # Guests cannot access any course chapter — redirect to cours page
        return redirect(f'/dashboard/cours/?guest_blocked=chapters')
    if not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())

    # Premium gate: free users can only access chapter 1 per subject
    from core.premium import can_access_chapter
    if not can_access_chapter(request.user, subject, num):
        return render(request, 'core/premium_required.html', {
            'feature': 'Chapitre verrouillé',
            'message': f'Les utilisateurs gratuits n\'ont accès qu\'au premier chapitre par matière. Passe au plan PRO pour débloquer tous les chapitres !',
        })

    chapters = _get_cours_chapters(subject)
    chapter = next((c for c in chapters if c.get('num') == num), None)

    if not chapter:
        # Fallback : ancienne URL avec chapter_id DB
        return redirect('cours')

    # Ajouter un alias 'description' pour compatibilité template (le JSON utilise 'summary')
    chapter = dict(chapter)
    chapter.setdefault('description', chapter.get('summary', ''))

    # Reprendre ou créer une session JSON-backed
    session = CourseSession.objects.filter(
        user=request.user, chapter_subject=subject, chapter_num=num, status='active'
    ).order_by('-updated_at').first()

    if not session:
        session = CourseSession.objects.create(
            user=request.user,
            chapter=None,
            chapter_subject=subject,
            chapter_num=num,
            chapter_title=chapter.get('title', ''),
            chapter_desc=chapter.get('summary', ''),
            messages=[],
            progress_step=0,
        )

    subject_info = MATS.get(subject, {'label': subject, 'color': '#10B981', 'icon': '📚'})

    import json as _json_mod

    # Get the chapter title from the chapter dict (which has real data from JSON)
    chapter_title = chapter.get('title', f'Chapitre {num}')
    chapter_desc = chapter.get('summary', '')

    hybrid_mode = False
    hybrid_course = {}
    hybrid_state = {'subchapter_idx': 0, 'chunk_idx': 0}
    
    # Try to get hybrid payload for ANY subject that has chapters
    if subject in MATS:
        if subject == 'maths':
            hybrid_course = pdf_loader.get_math_hybrid_course_payload(num)
        else:
            # For other subjects, try to extract chapter content and create hybrid payload
            note_content = pdf_loader.get_note_chapter_content(subject, num) or ''
            if note_content:
                hybrid_course = pdf_loader.get_generic_hybrid_course_payload(note_content, chapter_title)
        
        hybrid_mode = bool(hybrid_course.get('subchapters'))
        if hybrid_mode:
            hybrid_total = len(hybrid_course.get('subchapters', []))
            hybrid_state['subchapter_idx'] = min(max(int(session.progress_step or 0), 0), hybrid_total)
            try:
                progress = CourseProgressState.objects.filter(
                    user=request.user,
                    course_key=_hybrid_course_key(subject, num),
                ).first()
                if progress and isinstance(progress.state, dict):
                    hybrid_state['subchapter_idx'] = min(
                        max(int(progress.state.get('subchapter_idx', hybrid_state['subchapter_idx'])), 0),
                        hybrid_total,
                    )
                    if hybrid_state['subchapter_idx'] >= hybrid_total:
                        hybrid_state['chunk_idx'] = 0
                    else:
                        chunk_list = hybrid_course.get('subchapters', [])[hybrid_state['subchapter_idx']].get('chunks', [])
                        hybrid_state['chunk_idx'] = min(
                            max(int(progress.state.get('chunk_idx', 0)), 0),
                            max(0, len(chunk_list) - 1),
                        )
            except Exception:
                pass


    # Filter out internal meta-messages (roles starting with __) from the JSON
    # sent to the frontend — these are for backend logic only (plan, plan_intro…)
    _visible_msgs = [] if hybrid_mode else [m for m in session.messages if not str(m.get('role', '')).startswith('__')]

    # Compute total_steps so the progress bar is correct on page reload
    _total_steps = 1
    _plan_entry = next((m for m in session.messages if m.get('role') == '__plan__'), None)
    _plan_tasks = []
    if hybrid_mode:
        _plan_tasks = [str(sub.get('title') or '').strip() for sub in hybrid_course.get('subchapters', []) if str(sub.get('title') or '').strip()]
        _total_steps = max(1, len(_plan_tasks))
    elif _plan_entry:
        try:
            _tl = _json_mod.loads(_plan_entry.get('content', '[]'))
            if isinstance(_tl, list) and len(_tl) >= 2:
                _total_steps = len(_tl)
                _plan_tasks = _tl
        except Exception:
            pass

    return render(request, 'core/chapter_cours.html', {
        'chapter': chapter,
        'session': session,
        'session_id': session.pk,
        'subject': subject,
        'subject_info': subject_info,
        'messages': session.messages,
        'messages_json': _json_mod.dumps(_visible_msgs).replace('</', '<\/'),
        'progress_step': session.progress_step,
        'total_steps': _total_steps,
        'plan_tasks_json': _json_mod.dumps(_plan_tasks),
        'hybrid_mode': hybrid_mode,
        'hybrid_course_json': _json_mod.dumps(hybrid_course, ensure_ascii=False).replace('</', '<\/'),
        'hybrid_state_json': _json_mod.dumps(hybrid_state),
    })


@login_required
@require_POST
def api_course_chat(request):
    """API AJAX pour le cours interactif."""
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    session_id = data.get('session_id')
    user_msg   = data.get('message', '').strip()
    target_step = data.get('target_step')  # optional: jump to a specific plan step
    clarification_mode = bool(data.get('clarification_mode'))
    lesson_context = (data.get('lesson_context') or '').strip()[:3200]
    subchapter_title = (data.get('subchapter_title') or '').strip()
    chunk_title = (data.get('chunk_title') or '').strip()

    # Optional base64 image
    _img_b64  = data.get('image_b64', '')
    _img_mime = data.get('image_mime', 'image/jpeg')
    _image_data = None
    _image_mime = None
    if _img_b64:
        try:
            import base64 as _b64mod
            _image_data = _b64mod.b64decode(_img_b64)
            _image_mime = _img_mime or 'image/jpeg'
        except Exception:
            pass

    if not user_msg or not session_id:
        return JsonResponse({'error': 'Paramètres manquants'}, status=400)

    try:
        session = CourseSession.objects.get(pk=session_id, user=request.user)
    except CourseSession.DoesNotExist:
        return JsonResponse({'error': 'Session introuvable'}, status=404)

    # ── Récupérer les données du chapitre (JSON ou DB legacy) ─────────────
    subj = session.chapter_subject or (session.chapter.subject if session.chapter_id else 'general')
    chapter_title = session.chapter_title or (session.chapter.title if session.chapter_id else 'Chapitre')
    chapter_desc  = session.chapter_desc  or (session.chapter.description if session.chapter_id else '')
    exam_excerpts = ''

    if clarification_mode and lesson_context:
        current_step = 0
        if target_step is not None:
            try:
                current_step = max(0, int(target_step))
            except (TypeError, ValueError):
                current_step = 0
        else:
            current_step = max(0, int(session.progress_step or 0))

        hybrid_total_steps = 1
        if session.chapter_subject and session.chapter_num:
            if session.chapter_subject == 'maths':
                hybrid_payload = pdf_loader.get_math_hybrid_course_payload(session.chapter_num)
            else:
                note_content = pdf_loader.get_note_chapter_content(session.chapter_subject, session.chapter_num) or ''
                hybrid_payload = pdf_loader.get_generic_hybrid_course_payload(note_content, chapter_title)
            hybrid_total_steps = max(1, len(hybrid_payload.get('subchapters', [])))

        try:
            user_profile = gemini.build_user_learning_profile_short(request.user)
        except Exception:
            user_profile = ''

        try:
            reply = gemini.course_chunk_clarification(
                subject=subj,
                chapter_title=chapter_title,
                subchapter_title=subchapter_title or chapter_title,
                chunk_title=chunk_title or 'Point du cours',
                lesson_context=lesson_context,
                user_question=user_msg,
                user_profile=user_profile,
                messages=session.messages,
            )
        except Exception as _e:
            import logging as _logging
            _logging.getLogger(__name__).error('course_chunk_clarification error: %s', _e, exc_info=True)
            return JsonResponse({'error': f'Erreur IA : {type(_e).__name__}. Réessaie dans quelques secondes.'}, status=503)

        ts = __import__('time').strftime('%H:%M')
        session.messages.append({'role': 'user', 'content': user_msg, 'ts': ts})
        session.messages.append({'role': 'assistant', 'content': reply, 'ts': ts})
        session.progress_step = current_step
        session.save(update_fields=['messages', 'progress_step', 'updated_at'])

        return JsonResponse({
            'reply': reply,
            'followups': [],
            'new_step': current_step,
            'total_steps': hybrid_total_steps,
            'task_list': [],
            'auto_continue': False,
        })

    # ── Build chapter content for IA ──────────────────────────────────────────
    # anglais / espagnol / informatique : no JSON context (AI generates freely)
    # other subjects: use atomized note_*_ai.json blocks (FlexSearch-style)
    exam_excerpts = ''
    content_source_mode = 'notes'
    if session.chapter_subject and session.chapter_num:
        # Refresh chapter title from canonical catalogue
        note_chapters = _get_cours_chapters(session.chapter_subject)
        note_chap = next((c for c in note_chapters if c.get('num') == session.chapter_num), None)
        if note_chap:
            chapter_title = note_chap.get('title', chapter_title)

        if session.chapter_subject in _NO_JSON_CONTEXT_SUBJECTS:
            # No JSON context — AI answers from its own knowledge
            exam_excerpts = ''
            content_source_mode = 'no_context'
        else:
            # Try atomized AI blocks first (faster, less tokens)
            ai_context = _search_ai_blocks(
                session.chapter_subject,
                session.chapter_num,
                user_msg,
                max_blocks=12,
            )
            if ai_context:
                exam_excerpts = ai_context
                content_source_mode = 'ai_blocks'
            else:
                # Fallback: full note chapter content
                note_content = pdf_loader.get_note_chapter_content(session.chapter_subject, session.chapter_num)
                if note_content:
                    exam_excerpts = note_content
                    content_source_mode = 'notes'

            # For science subjects without notes: try exercises as fallback
            if not exam_excerpts and session.chapter_subject in ('maths', 'physique', 'chimie'):
                content_source_mode = 'exo_fallback'
                try:
                    from . import exo_loader as _exo_for_content
                    chapter_title_for_exos = chapter_title or ''
                    training_exos = _exo_for_content.get_exercises(session.chapter_subject, chapter_title_for_exos, n=8)
                    if training_exos:
                        exos_block = (
                            f"\n\nCONTENU OFFICIEL DU CHAPITRE (source exercices BAC)\n"
                            f"Chapitre étudié : {chapter_title_for_exos}\n"
                            "───────────────────────────────────────────────────\n"
                        )
                        for i, exo in enumerate(training_exos, 1):
                            exos_block += f"\nExercice BAC {i} — {exo.get('source_display', 'Source inconnue')}\n"
                            theme = exo.get('theme', exo.get('chapter', 'N/A'))
                            if theme:
                                exos_block += f"Compétence visée : {theme}\n"
                            if exo.get('intro'):
                                exos_block += f"Situation : {exo['intro'][:500]}...\n" if len(exo['intro']) > 500 else f"Situation : {exo['intro']}\n"
                            if exo.get('questions'):
                                exos_block += "Questions-type :\n"
                                for q in exo['questions'][:3]:
                                    exos_block += f"- {q}\n"
                        exam_excerpts = exos_block
                except Exception:
                    pass
    elif session.chapter_id:
        exam_excerpts = session.chapter.exam_excerpts

    # Profil utilisateur
    try:
        user_profile = gemini.build_user_learning_profile_short(request.user)  # SHORT profile
    except Exception:
        user_profile = ''

    # ── Plan pédagogique — généré une fois, caché dans la session ──────────────────
    chapter_task_list = None
    plan_invalidated = False
    plan_version = 11  # maths now uses deterministic JSON subchapter plan
    _plan_entry = next((m for m in session.messages if m.get('role') == '__plan__'), None)
    if _plan_entry:
        try:
            same_title = (_plan_entry.get('chapter_title') or '') == (chapter_title or '')
            same_source = (_plan_entry.get('plan_source') or '') == content_source_mode
            same_version = int(_plan_entry.get('plan_version') or 0) == plan_version
            if same_title and same_source and same_version:
                chapter_task_list = json.loads(_plan_entry.get('content', '[]'))
            else:
                plan_invalidated = True
                chapter_task_list = None
        except Exception:
            chapter_task_list = None
            plan_invalidated = True

    if plan_invalidated:
        # Reset stale conversation context that was built with a different chapter mapping/source
        session.messages = [m for m in session.messages if str(m.get('role', '')).startswith('__') and m.get('role') != '__plan__']
        session.progress_step = 0

    if not chapter_task_list and exam_excerpts:
        try:
            if subj == 'maths' and session.chapter_num:
                chapter_task_list = pdf_loader.get_math_chapter_plan_from_note(session.chapter_num)
            if not chapter_task_list:
                chapter_task_list = gemini.generate_chapter_task_list(subj, chapter_title, exam_excerpts)
            if chapter_task_list:
                session.messages = [m for m in session.messages if m.get('role') != '__plan__']
                session.messages.insert(0, {
                    'role': '__plan__',
                    'chapter_title': chapter_title,
                    'plan_source': content_source_mode,
                    'plan_version': plan_version,
                    'content': json.dumps(chapter_task_list, ensure_ascii=False),
                })
                # Safety: if plan was regenerated mid-session, clamp progress_step
                # to avoid pointing beyond the new plan's concept list
                if session.progress_step > len(chapter_task_list):
                    session.progress_step = len(chapter_task_list)
        except Exception:
            chapter_task_list = None

    # ── Free navigation: jump to a specific plan step ─────────────────────────
    if target_step is not None:
        try:
            target_idx = int(target_step)
            if chapter_task_list and 0 <= target_idx < len(chapter_task_list):
                session.progress_step = target_idx
        except (TypeError, ValueError):
            pass

    try:
        result = gemini.course_chat(
            chapter_title=chapter_title,
            chapter_description=chapter_desc,
            exam_excerpts=exam_excerpts,
            subject=subj,
            messages=session.messages,
            user_message=user_msg,
            progress_step=session.progress_step,
            user_profile=user_profile,
            chapter_task_list=chapter_task_list,
            image_data=_image_data,
            image_mime=_image_mime,
        )
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).error('course_chat error: %s', _e, exc_info=True)
        return JsonResponse({'error': f'Erreur IA : {type(_e).__name__}. Réessaie dans quelques secondes.'}, status=503)

    if not result.get('reply'):
        return JsonResponse({'error': "L'IA n'a pas pu générer de réponse. Réessaie."})

    import time as _time
    ts = _time.strftime('%H:%M')
    _is_auto_continue_msg = (user_msg.strip() in ('[AUTO_CONTINUE]', '[REGEN_TRUNCATED]'))
    if not _is_auto_continue_msg:
        session.messages.append({'role': 'user', 'content': user_msg, 'ts': ts})
    session.messages.append({'role': 'assistant', 'content': result['reply'], 'ts': ts})
    session.progress_step = result['new_step']

    # Hidden controlled-generation state for backend chunk continuation
    session.messages = [m for m in session.messages if m.get('role') != '__chunk_state__']
    if result.get('chunk_meta'):
        try:
            session.messages.append({'role': '__chunk_state__', 'content': json.dumps(result['chunk_meta'])})
        except Exception:
            pass

    # Manage __plan_intro__ marker: added after plan-only first message,
    # removed once concept teaching actually begins
    if result.get('plan_intro'):
        # Plan was shown this turn — add marker so next call knows to teach concept 0
        if not any(m.get('role') == '__plan_intro__' for m in session.messages):
            session.messages.append({'role': '__plan_intro__'})
    else:
        # Concept teaching happened (or synthesis) — remove the marker
        session.messages = [m for m in session.messages if m.get('role') != '__plan_intro__']

    if len(session.messages) > 80:
        # Preserve internal metadata roles (__plan__, __plan_intro__, etc.) when trimming
        meta_msgs = [m for m in session.messages if str(m.get('role', '')).startswith('__')]
        chat_msgs = [m for m in session.messages if not str(m.get('role', '')).startswith('__')]
        chat_msgs = chat_msgs[-70:]  # Keep last 70 chat messages (leave room for meta)
        session.messages = meta_msgs + chat_msgs

    session.save(update_fields=['messages', 'progress_step', 'updated_at'])

    # Mémorisation asynchrone — extrait les observations sur l'élève
    try:
        import threading as _threading
        _threading.Thread(
            target=gemini.extract_and_save_memories,
            args=(request.user, user_msg, result['reply'], subj),
            daemon=True,
        ).start()
    except Exception:
        pass

    return JsonResponse({
        'reply':         result['reply'],
        'followups':     [],
        'new_step':      result['new_step'],
        'total_steps':   result.get('total_steps', 3),
        'task_list':     chapter_task_list or [],
        'auto_continue': bool(result.get('auto_continue')),
    })


@login_required
@require_POST
def api_course_reset(request):
    """Réinitialise une session de cours (recommencer depuis le début)."""
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    session_id = data.get('session_id')
    try:
        session = CourseSession.objects.get(pk=session_id, user=request.user)
        session.messages      = []
        session.progress_step = 0
        session.status        = 'active'
        session.save(update_fields=['messages', 'progress_step', 'status', 'updated_at'])
        CourseProgressState.objects.filter(
            user=request.user,
            course_key=_hybrid_course_key(session.chapter_subject or '', session.chapter_num or 0),
        ).delete()
        return JsonResponse({'ok': True})
    except CourseSession.DoesNotExist:
        return JsonResponse({'error': 'Session introuvable'}, status=404)


@login_required
@require_POST
def api_course_hybrid_progress(request):
    data, _err = _parse_json_body(request)
    if _err:
        return _err

    session_id = data.get('session_id')
    subchapter_idx = data.get('subchapter_idx', 0)
    chunk_idx = data.get('chunk_idx', 0)

    try:
        session = CourseSession.objects.get(pk=session_id, user=request.user)
    except CourseSession.DoesNotExist:
        return JsonResponse({'error': 'Session introuvable'}, status=404)

    if session.chapter_subject != 'maths' or not session.chapter_num:
        return JsonResponse({'error': 'Mode hybride indisponible pour cette matière.'}, status=400)

    hybrid_payload = pdf_loader.get_math_hybrid_course_payload(session.chapter_num)
    subchapters = hybrid_payload.get('subchapters', [])
    if not subchapters:
        return JsonResponse({'error': 'Cours hybride indisponible.'}, status=400)

    try:
        subchapter_idx = max(0, min(int(subchapter_idx), len(subchapters)))
    except (TypeError, ValueError):
        subchapter_idx = 0
    if subchapter_idx >= len(subchapters):
        chunk_idx = 0
    else:
        try:
            chunk_idx = max(0, min(int(chunk_idx), len(subchapters[subchapter_idx].get('chunks', [])) - 1))
        except (TypeError, ValueError):
            chunk_idx = 0

    CourseProgressState.objects.update_or_create(
        user=request.user,
        course_key=_hybrid_course_key(session.chapter_subject, session.chapter_num),
        defaults={'state': {'subchapter_idx': subchapter_idx, 'chunk_idx': chunk_idx}},
    )
    session.progress_step = subchapter_idx
    session.save(update_fields=['progress_step', 'updated_at'])

    return JsonResponse({'ok': True, 'subchapter_idx': subchapter_idx, 'chunk_idx': chunk_idx})


@login_required
@require_POST
def api_course_section(request):
    """
    Génère le contenu IA pour un SOUS-CHAPITRE individuel.
    Appelé progressivement au fur et à mesure que l'élève avance.
    POST body: {session_id, section_title, section_idx, mode, weak_points}
    """
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    session_id    = data.get('session_id')
    section_title = data.get('section_title', '')
    mode          = data.get('mode', 'normal')   # 'normal' | 'remediation'
    weak_points   = data.get('weak_points', [])

    if not section_title or not session_id:
        return JsonResponse({'error': 'Paramètres manquants'}, status=400)

    try:
        session = CourseSession.objects.get(pk=session_id, user=request.user)
    except CourseSession.DoesNotExist:
        return JsonResponse({'error': 'Session introuvable'}, status=404)

    try:
        subj          = session.chapter_subject or 'general'
        chapter_title = session.chapter_title or 'Cours'

        # Contexte du chapitre : priorité absolue au contenu complet des notes locales
        chapter_context = ''
        if session.chapter_subject and session.chapter_num:
            full_note = pdf_loader.get_note_chapter_content(session.chapter_subject, session.chapter_num)
            if full_note:
                chapter_context = _extract_relevant_note_section(full_note, section_title, max_chars=7000)
        if not chapter_context:
            chapters = pdf_loader.get_chapters_from_json(subj)
            json_chap = next((c for c in chapters if c.get('num') == session.chapter_num), None)
            if json_chap:
                parts = []
                if json_chap.get('competences'):
                    parts.append('Compétences : ' + ', '.join(json_chap['competences'][:4]))
                if json_chap.get('contenus'):
                    parts.append('Contenus : ' + ', '.join(json_chap['contenus'][:8]))
                if json_chap.get('summary'):
                    parts.append('Résumé : ' + json_chap['summary'][:400])
                chapter_context = '\n'.join(parts)

        # ── Check cache first ──────────────────────────────────────────
        cache_key      = f'__section_{section_title}_{mode}'
        cached_entry   = next((m for m in (session.messages or [])
                               if m.get('_cache_key') == cache_key), None)
        if cached_entry:
            return JsonResponse({
                'content': cached_entry.get('content', ''),
                'section_title': section_title,
                'has_exam_data': False,
                'exam_snippet': '',
                'cached': True,
            })

        # Langue de l'élève (depuis header ou profil)
        user_lang = _get_user_lang(request)

        # Chercher des extraits d'examens liés à cette section
        exam_related = pdf_loader.get_exam_text_for_section(subj, section_title, chapter_title)

        # Générer le contenu
        content = gemini.generate_section_content(
            chapter_title=chapter_title,
            section_title=section_title,
            subject=subj,
            chapter_context=chapter_context,
            mode=mode,
            weak_points=weak_points if weak_points else None,
            exam_related=exam_related,
            user_lang=user_lang,
        )

        # Sauvegarder dans la session
        session_data = session.messages or []
        if not any(m.get('_cache_key') == cache_key for m in session_data):
            session.messages = session_data + [{
                '_cache_key': cache_key,
                'role': '__section_cache',
                'section_title': section_title,
                'mode': mode,
                'content': content,
            }]
            session.save(update_fields=['messages', 'updated_at'])

        return JsonResponse({
            'content': content,
            'section_title': section_title,
            'has_exam_data': bool(exam_related),
            'exam_snippet': exam_related[:150] if exam_related else '',
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_course_miniquiz(request):
    """
    Génère un mini-quiz (3-4 QCM) pour un sous-chapitre.
    POST body: {session_id, section_title, mode, weak_points}
    """
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    session_id    = data.get('session_id')
    section_title = data.get('section_title', '')
    mode          = data.get('mode', 'normal')
    weak_points   = data.get('weak_points', [])

    if not section_title or not session_id:
        return JsonResponse({'error': 'Paramètres manquants'}, status=400)

    try:
        session = CourseSession.objects.get(pk=session_id, user=request.user)
    except CourseSession.DoesNotExist:
        return JsonResponse({'error': 'Session introuvable'}, status=404)

    try:
        subj          = session.chapter_subject or 'general'
        chapter_title = session.chapter_title or 'Cours'

        # Langue de l'élève (depuis header ou profil)
        user_lang = _get_user_lang(request)

        # Extraits d'examens liés
        exam_related = pdf_loader.get_exam_text_for_section(subj, section_title, chapter_title)

        # Générer le mini-quiz
        count = 3 if mode == 'remediation' else 4
        questions = gemini.generate_section_miniquiz(
            section_title=section_title,
            chapter_title=chapter_title,
            subject=subj,
            count=count,
            mode=mode,
            weak_points=weak_points if weak_points else None,
            exam_related=exam_related,
            user_lang=user_lang,
        )

        # Chercher des questions d'examen réelles liées
        exam_qs = []
        if exam_related:
            raw_eqs = gemini.find_exam_questions_for_section(
                section_title=section_title,
                chapter_title=chapter_title,
                subject=subj,
                exam_json_text=exam_related,
            )
            exam_qs = raw_eqs[:2]

        return JsonResponse({
            'questions': questions,
            'exam_questions': exam_qs,
            'section_title': section_title,
            'mode': mode,
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


# ─────────────────────────────────────────────
# TRADUCTION KREYÒL / FRANÇAIS
# ─────────────────────────────────────────────

@login_required
@require_POST
def api_translate(request):
    """
    Traduit des textes FR → Kreyòl via IA (llama-3.3-70b).
    Résultats mis en cache dans TranslationCache pour éviter de rappeler l'IA.
    Body JSON: { "texts": ["...", "..."], "lang": "kr", "context": "chat_suggestions" }
    """
    import json as _json
    from .models import TranslationCache

    try:
        body  = _json.loads(request.body)
        texts = body.get('texts', [])
        lang  = body.get('lang', 'kr')
        ctx   = body.get('context', '')
    except Exception:
        return JsonResponse({'error': 'invalid json'}, status=400)

    if not texts or lang == 'fr':
        return JsonResponse({'translations': texts, 'cached': True})

    results = [None] * len(texts)
    to_translate = []

    for i, text in enumerate(texts):
        if not text or not text.strip():
            results[i] = text
            continue
        cached = TranslationCache.get_or_none(text, lang)
        if cached:
            # Éviction des entrées empoisonnées (traduction = source = échec silencieux passé)
            if cached.translated.strip() == cached.source_text.strip():
                cached.delete()  # Supprimer l'entrée invalide, on va retraduire
            else:
                results[i] = cached.translated
                continue
        to_translate.append((i, text))

    if to_translate:
        batch_translated = gemini.translate_batch(
            [t for _, t in to_translate], lang, ctx
        )
        for (idx, orig), translated in zip(to_translate, batch_translated):
            # Ne stocker dans le cache QUE si la traduction est réellement différente
            # Évite de polluer le cache avec des "traductions" identiques à l'original (erreurs silencieuses)
            if translated and translated.strip() and translated.strip() != orig.strip():
                TranslationCache.store(orig, lang, translated, ctx)
            results[idx] = translated

    return JsonResponse({'translations': results, 'cached': len(to_translate) == 0})


@login_required
@require_POST
def api_course_question(request):
    """AJAX endpoint pour poser une question sur un chapitre (avec historique de conversation)"""
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        course = data.get('course', '').strip()
        context = data.get('context', '').strip()
        history = data.get('history', [])  # [{role:'user'|'assistant', content:'...'}]

        if not question or not course:
            return JsonResponse({'error': 'Données manquantes'}, status=400)

        context_truncated = context[:2000] if context else ''

        # Rebuild conversation history as text block (last 8 turns max)
        history_block = ''
        for msg in history[-8:]:
            role_label = 'Élève' if msg.get('role') == 'user' else 'Tuteur'
            content = (msg.get('content') or '').strip()
            if content:
                history_block += f'{role_label}: {content}\n\n'

        history_section = (
            f'\n\n---HISTORIQUE DE LA CONVERSATION---\n{history_block.strip()}\n---FIN HISTORIQUE---'
            if history_block else ''
        )

        system_prompt = (
            f'Tu es un tuteur expert en {course} pour le baccalauréat haïtien.\n'
            f'Réponds de manière claire, pédagogique et concise. '
            f'TIENS COMPTE de tout l\'historique de la conversation pour donner des réponses cohérentes.\n'
            + (f'\n---CONTENU DU CHAPITRE---\n{context_truncated}\n---FIN CONTENU---\n' if context_truncated else '')
            + history_section
            + '\n\nRègles: sois encourageant, va droit au but. '
            + 'Si la réponse nécessite une formule, écris-la entre $ (ex: $F=ma$). '
            + 'Réponds UNIQUEMENT à la dernière question de l\'élève en restant cohérent avec la conversation.'
        )

        response = gemini.respond(system_prompt, question, max_tokens=900)

        return JsonResponse({'answer': response.strip(), 'success': True})
    except Exception as e:
        import logging
        logging.exception("Error in api_course_question")
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_chapter_summary(request):
    """AJAX endpoint pour obtenir/générer un résumé de chapitre.

    Génère le résumé une seule fois via l'IA, le sauvegarde en base,
    puis le sert depuis la base pour tous les utilisateurs suivants.
    """
    try:
        data = json.loads(request.body)
        course = data.get('course', '').strip().lower()
        chapter_num = int(data.get('chapter_num', 0))
        chapter_title = data.get('chapter_title', '').strip()
        context = data.get('context', '').strip()

        if not course or not chapter_num or not context:
            return JsonResponse({'error': 'Données manquantes'}, status=400)

        section_id = f'chapter-{chapter_num}'

        # Check cache first (shared across all users)
        cached = GeneratedCourseAsset.objects.filter(
            course_key=course,
            section_id=section_id,
            asset_type='summary',
            mode='normal',
        ).first()

        if cached and cached.payload.get('summary'):
            return JsonResponse({'summary': cached.payload['summary'], 'cached': True, 'success': True})

        # Generate via AI
        context_truncated = context[:4000]
        prompt = (
            f"Fais un résumé concis et mémorisable du chapitre '{chapter_title}' "
            f"pour un élève préparant le baccalauréat haïtien en {course}. "
            f"Structure: 3-5 points clés numérotés, chacun en 1-2 phrases, "
            f"mets en valeur les formules et définitions importantes. "
            f"Sois direct et pédagogique."
        )
        system_prompt = f"Tu es un tuteur expert en {course} pour le bac haïtien.\n\n---CONTENU---\n{context_truncated}\n---FIN---"
        summary = gemini.respond(system_prompt, prompt, max_tokens=600)

        # Cache in DB (shared for all users)
        GeneratedCourseAsset.objects.update_or_create(
            course_key=course,
            section_id=section_id,
            asset_type='summary',
            mode='normal',
            defaults={
                'section_title': chapter_title,
                'payload': {'summary': summary.strip()},
            }
        )

        return JsonResponse({'summary': summary.strip(), 'cached': False, 'success': True})

    except Exception as e:
        import logging
        logging.exception("Error in api_chapter_summary")
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


@login_required
@require_POST
def api_generate_exercises(request):
    """AJAX endpoint pour générer des exercices"""
    try:
        data = json.loads(request.body)
        course = data.get('course', '').strip()
        chapter = data.get('chapter', '').strip()
        difficulty = data.get('difficulty', 'normal').strip()
        count = int(data.get('count', 5))
        context = data.get('context', '').strip()
        
        if not course or not context or count < 1 or count > 20:
            return JsonResponse({'error': 'Paramètres invalides'}, status=400)
        
        # Construire le prompt pour Gemini
        prompt = f"""Tu es un expert en création d'exercices pédagogiques pour le baccalauréat haïtien.

Basé sur le contexte suivant du cours de {course}:
---CONTEXTE---
{context[:1500]}
---FIN CONTEXTE---

Génère {count} exercices de difficulté "{difficulty}" (facile, normal ou difficile).

IMPORTANT: Ne fournis QUE les questions, pas les réponses ou solutions.

Format tes réponses comme une liste JSON:
[
  {{"question": "...", "difficulty": "...", "type": "..."}},
  ...
]"""

        response = gemini.respond('Tu es un formateur expert', prompt)
        
        # Essayer de parser JSON
        exercises = []
        try:
            # Chercher un array JSON dans la réponse
            import re
            match = re.search(r'\[.*\]', response, re.DOTALL)
            if match:
                exercises = json.loads(match.group())
        except:
            # Si le parsing échoue, créer des exercices simples
            lines = response.split('\n')
            for i, line in enumerate(lines[:count]):
                if line.strip():
                    exercises.append({
                        'question': line.strip(),
                        'type': 'question_ouverte',
                        'difficulty': difficulty,
                    })
        
        return JsonResponse({
            'exercises': exercises[:count],
            'success': True,
        })
    except Exception as e:
        import logging
        logging.exception("Error in api_generate_exercises")
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


def amis_view(request):
    from accounts.models import Friendship
    from django.contrib.auth.models import User as DUser
    if not request.user.is_authenticated:
        if _is_guest(request):
            return render(request, 'core/amis.html', {
                'is_guest': True,
                'profile': None,
                'friends': [],
                'pending_in': [],
                'pending_sent': [],
                'suggestions': [],
            })
        return redirect('/login/?next=' + request.get_full_path())
    from django.db.models import Q
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Friends list (both directions)
    accepted = Friendship.objects.filter(
        Q(from_user=request.user, status='accepted') |
        Q(to_user=request.user, status='accepted')
    ).filter(
        from_user__is_staff=False,
        to_user__is_staff=False,
        from_user__agent__isnull=True,
        to_user__agent__isnull=True,
    ).select_related('from_user__profile', 'to_user__profile')

    friends = []
    for f in accepted:
        other = f.to_user if f.from_user == request.user else f.from_user
        try: other_prof = other.profile
        except Exception: other_prof = None
        friends.append({'user': other, 'profile': other_prof, 'friendship_id': f.id})

    # Pending incoming requests
    pending_in = Friendship.objects.filter(
        to_user=request.user, status='pending'
    ).filter(
        from_user__is_staff=False,
        from_user__agent__isnull=True,
    ).select_related('from_user__profile')

    # Pending sent requests
    pending_sent = Friendship.objects.filter(
        from_user=request.user, status='pending'
    ).filter(
        to_user__is_staff=False,
        to_user__agent__isnull=True,
    ).select_related('to_user__profile')

    # Suggestions (same school or serie)
    friend_ids = {f['user'].id for f in friends}
    friend_ids.add(request.user.id)
    # Also exclude users with any active relation (pending/accepted in both directions)
    pending_sent_ids = set(
        Friendship.objects.filter(from_user=request.user, status='pending')
        .values_list('to_user_id', flat=True)
    )
    pending_in_ids = set(
        Friendship.objects.filter(to_user=request.user, status='pending')
        .values_list('from_user_id', flat=True)
    )
    exclude_ids = friend_ids | pending_sent_ids | pending_in_ids

    suggestions = []
    try:
        qs = UserProfile.objects.exclude(user__id__in=exclude_ids).filter(
            user__is_staff=False,
            user__agent__isnull=True,
            user__is_active=True,
        ).select_related('user')
        if profile.school:
            qs = qs.filter(school=profile.school)[:8]
        elif profile.serie:
            qs = qs.filter(serie=profile.serie)[:8]
        else:
            qs = qs[:8]
        suggestions = list(qs)
    except Exception:
        pass

    # Get last message for each friend (for WhatsApp-style preview)
    from accounts.models import FriendMessage
    from core.premium import is_premium as _is_prem
    user_is_premium = _is_prem(request.user)

    for f in friends:
        other = f['user']
        last_msg = FriendMessage.objects.filter(
            models.Q(sender=request.user, receiver=other) |
            models.Q(sender=other, receiver=request.user)
        ).order_by('-created_at').first()
        f['last_message'] = last_msg
        f['unread_count'] = FriendMessage.objects.filter(
            sender=other, receiver=request.user, is_read=False
        ).count()

    # Sort friends by last message time (most recent first)
    friends.sort(key=lambda f: f['last_message'].created_at if f['last_message'] else f['user'].date_joined, reverse=True)

    # Admin motivational message (OUTOUBON)
    from accounts.models import AdminMessage
    admin_msg = AdminMessage.objects.filter(receiver=request.user).first()  # latest (ordered -created_at)
    admin_unread = AdminMessage.objects.filter(receiver=request.user, is_read=False).exists()

    return render(request, 'core/amis.html', {
        'profile': profile,
        'friends': friends,
        'pending_in': pending_in,
        'pending_sent': pending_sent,
        'suggestions': suggestions,
        'is_premium': user_is_premium,
        'admin_msg': admin_msg,
        'admin_unread': admin_unread,
    })


@login_required
def api_friend_request(request):
    from accounts.models import Friendship
    from django.contrib.auth.models import User as DUser
    from django.db.models import Q

    if request.method == 'GET':
        # Search users
        q = request.GET.get('search', '').strip()
        if len(q) < 2:
            return JsonResponse({'users': []})
        users = list(DUser.objects.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
        ).exclude(id=request.user.id).filter(
            is_staff=False,
            is_active=True,
            agent__isnull=True,
        )[:10])
        user_ids = [u.id for u in users]
        rel_qs = Friendship.objects.filter(
            Q(from_user=request.user, to_user_id__in=user_ids) |
            Q(to_user=request.user, from_user_id__in=user_ids)
        )
        relation_map = {}
        for rel in rel_qs:
            other_id = rel.to_user_id if rel.from_user_id == request.user.id else rel.from_user_id
            status = ''
            if rel.status == 'accepted':
                status = 'friends'
            elif rel.status == 'pending':
                status = 'pending_sent' if rel.from_user_id == request.user.id else 'pending_received'
            relation_map[other_id] = {'status': status, 'friendship_id': rel.id}

        result = []
        for u in users:
            try: p = u.profile
            except Exception: p = None
            rel = relation_map.get(u.id, {'status': '', 'friendship_id': None})
            result.append({
                'id': u.id,
                'username': u.username,
                'name': u.get_full_name() or u.username,
                'school': p.school if p else '',
                'serie': p.serie if p else '',
                'relation_status': rel['status'],
                'friendship_id': rel['friendship_id'],
            })
        return JsonResponse({'users': result})

    # POST handling
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    action = data.get('action', '')
    user_id = data.get('user_id')
    friendship_id = data.get('friendship_id')

    if action == 'send':
        try:
            to_user = DUser.objects.get(id=user_id)
            if to_user == request.user:
                return JsonResponse({'error': 'Cannot add yourself'}, status=400)

            existing = Friendship.objects.filter(
                Q(from_user=request.user, to_user=to_user) |
                Q(from_user=to_user, to_user=request.user)
            ).first()

            if existing:
                if existing.status == 'accepted':
                    return JsonResponse({'error': 'Déjà amis'}, status=400)
                if existing.status == 'pending':
                    if existing.from_user_id == request.user.id:
                        return JsonResponse({'ok': True, 'created': False, 'status': 'pending'})
                    # Invitation croisée : on accepte automatiquement.
                    existing.status = 'accepted'
                    existing.save(update_fields=['status', 'updated_at'])
                    return JsonResponse({'ok': True, 'created': False, 'status': 'accepted', 'auto_accepted': True})
                # declined -> on relance proprement dans le sens courant
                if existing.from_user_id != request.user.id or existing.to_user_id != to_user.id:
                    existing.from_user = request.user
                    existing.to_user = to_user
                existing.status = 'pending'
                existing.save(update_fields=['from_user', 'to_user', 'status', 'updated_at'])
                return JsonResponse({'ok': True, 'created': False, 'status': 'pending'})

            f = Friendship.objects.create(from_user=request.user, to_user=to_user, status='pending')
            return JsonResponse({'ok': True, 'created': True, 'status': f.status})
        except DUser.DoesNotExist:
            return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)

    elif action == 'accept':
        try:
            f = Friendship.objects.get(id=friendship_id, to_user=request.user)
            f.status = 'accepted'
            f.save()
            return JsonResponse({'ok': True})
        except Friendship.DoesNotExist:
            return JsonResponse({'error': 'Demande introuvable'}, status=404)

    elif action == 'decline':
        try:
            f = Friendship.objects.get(id=friendship_id, to_user=request.user)
            f.delete()
            return JsonResponse({'ok': True})
        except Friendship.DoesNotExist:
            return JsonResponse({'error': 'Demande introuvable'}, status=404)

    elif action == 'remove':
        Friendship.objects.filter(
            Q(from_user=request.user, to_user__id=user_id) |
            Q(to_user=request.user, from_user__id=user_id)
        ).delete()
        return JsonResponse({'ok': True})

    elif action == 'cancel':
        Friendship.objects.filter(
            from_user=request.user,
            to_user__id=user_id,
            status='pending'
        ).delete()
        return JsonResponse({'ok': True})

    return JsonResponse({'error': 'Action invalide'}, status=400)


# ─────────────────── CHAT AMIS ───────────────────

@login_required
def api_friend_messages(request, friend_id):
    """GET: retourne les messages entre l'utilisateur et un ami."""
    from accounts.models import FriendMessage, Friendship
    from django.contrib.auth.models import User as DUser

    try:
        friend = DUser.objects.get(pk=friend_id)
    except DUser.DoesNotExist:
        return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)

    # Vérifier qu'ils sont amis
    is_friend = Friendship.objects.filter(
        models.Q(from_user=request.user, to_user=friend, status='accepted') |
        models.Q(from_user=friend, to_user=request.user, status='accepted')
    ).exists()
    if not is_friend:
        return JsonResponse({'error': 'Non ami'}, status=403)

    # Marquer les messages reçus comme lus
    FriendMessage.objects.filter(
        sender=friend, receiver=request.user, is_read=False
    ).update(is_read=True)

    # Récupérer les 50 derniers messages
    messages = FriendMessage.objects.filter(
        models.Q(sender=request.user, receiver=friend) |
        models.Q(sender=friend, receiver=request.user)
    ).order_by('-created_at')[:50]

    msgs = [{
        'id': m.id,
        'sender_id': m.sender_id,
        'content': m.content,
        'created_at': m.created_at.strftime('%H:%M'),
        'date': m.created_at.strftime('%d/%m/%Y'),
        'is_mine': m.sender_id == request.user.id,
        'is_read': m.is_read,
    } for m in reversed(list(messages))]

    return JsonResponse({'ok': True, 'messages': msgs})


@login_required
@require_POST
def api_friend_send_message(request):
    """POST: envoie un message à un ami."""
    from accounts.models import FriendMessage, Friendship
    from core.premium import is_premium, premium_required_json
    from django.contrib.auth.models import User as DUser

    # Premium gate
    if not is_premium(request.user):
        return JsonResponse(premium_required_json(), status=403)

    data, _err = _parse_json_body(request)
    if _err:
        return _err
    friend_id = data.get('friend_id')
    content = data.get('content', '').strip()

    if not content or len(content) > 2000:
        return JsonResponse({'error': 'Message invalide'}, status=400)

    try:
        friend = DUser.objects.get(pk=friend_id)
    except DUser.DoesNotExist:
        return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)

    # Vérifier amitié
    is_friend = Friendship.objects.filter(
        models.Q(from_user=request.user, to_user=friend, status='accepted') |
        models.Q(from_user=friend, to_user=request.user, status='accepted')
    ).exists()
    if not is_friend:
        return JsonResponse({'error': 'Non ami'}, status=403)

    msg = FriendMessage.objects.create(
        sender=request.user,
        receiver=friend,
        content=content,
    )

    return JsonResponse({
        'ok': True,
        'message': {
            'id': msg.id,
            'content': msg.content,
            'created_at': msg.created_at.strftime('%H:%M'),
            'date': msg.created_at.strftime('%d/%m/%Y'),
            'is_mine': True,
        }
    })


@login_required
def api_friend_unread_count(request):
    """GET: nombre total de messages non lus."""
    from accounts.models import FriendMessage
    count = FriendMessage.objects.filter(receiver=request.user, is_read=False).count()
    return JsonResponse({'count': count})


@login_required
def api_admin_message(request):
    """GET: fetch latest OUTOUBON message for user. POST (superuser): send motivational message."""
    from accounts.models import AdminMessage

    if request.method == 'POST':
        if not request.user.is_superuser:
            return JsonResponse({'error': 'Non autorisé'}, status=403)
        data, _err = _parse_json_body(request)
        if _err:
            return _err
        receiver_id = data.get('receiver_id')
        content = data.get('content', '').strip()
        broadcast = data.get('broadcast', False)

        if not content:
            return JsonResponse({'error': 'Message vide'}, status=400)

        from django.contrib.auth.models import User as DUser
        if broadcast:
            # Send to all users
            users = DUser.objects.filter(is_active=True)
            for u in users:
                AdminMessage.objects.create(receiver=u, content=content)
            return JsonResponse({'ok': True, 'count': users.count()})
        else:
            if not receiver_id:
                return JsonResponse({'error': 'receiver_id requis'}, status=400)
            try:
                receiver = DUser.objects.get(pk=receiver_id)
            except DUser.DoesNotExist:
                return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)
            AdminMessage.objects.create(receiver=receiver, content=content)
            return JsonResponse({'ok': True})

    # GET: return latest admin message
    msg = AdminMessage.objects.filter(receiver=request.user).order_by('-created_at').first()
    if msg:
        if not msg.is_read:
            msg.is_read = True
            msg.save(update_fields=['is_read'])
        return JsonResponse({
            'ok': True,
            'message': {
                'content': msg.content,
                'created_at': msg.created_at.strftime('%H:%M'),
                'date': msg.created_at.strftime('%d/%m/%Y'),
            }
        })
    return JsonResponse({'ok': True, 'message': None})


@login_required
def api_user_lang(request):
    """GET: retourne la langue préférée. POST: met à jour."""
    import json as _json
    if request.method == 'POST':
        try:
            body = _json.loads(request.body)
            lang = body.get('lang', 'fr')
            if lang not in ('fr', 'kr'):
                lang = 'fr'
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            profile.preferred_lang = lang
            profile.save(update_fields=['preferred_lang'])
            return JsonResponse({'ok': True, 'lang': lang})
        except Exception as e:
            _logger.exception('Server error')
            return JsonResponse({'error': 'Erreur interne du serveur.'}, status=400)
    try:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        lang = profile.preferred_lang or 'fr'
    except Exception:
        lang = 'fr'
    return JsonResponse({'lang': lang})


@require_POST
def api_exercise_chat(request):
    """
    Chat IA guidé pour un exercice.
    POST: {exercise: {...}, subject: str, messages: [...], message: str, student_name: str, image_b64?: str, image_mime?: str}
    Retourne: {response: str}
    """
    if not request.user.is_authenticated and not _is_guest(request):
        return JsonResponse({'error': 'login_required'}, status=401)
    try:
        data = json.loads(request.body)
        exercise = data.get('exercise', {})
        subject = data.get('subject', 'maths')
        messages = data.get('messages', [])  # historique [{role, content}]
        user_message = data.get('message', '').strip()
        student_name = data.get('student_name', 'l\'élève')

        # Optional base64 image
        _ex_img_b64  = data.get('image_b64', '')
        _ex_img_mime = data.get('image_mime', 'image/jpeg')
        _ex_image_data = None
        _ex_image_mime = None
        if _ex_img_b64:
            try:
                import base64 as _b64mod
                _ex_image_data = _b64mod.b64decode(_ex_img_b64)
                _ex_image_mime = _ex_img_mime or 'image/jpeg'
            except Exception:
                pass

        if not user_message:
            return JsonResponse({'error': 'Message vide'}, status=400)

        # Build exercise context
        intro = exercise.get('intro') or exercise.get('enonce', '')
        questions = exercise.get('questions', [])
        hints = exercise.get('hints', [])
        solution = exercise.get('solution', '')
        theme = exercise.get('theme', subject)
        source = exercise.get('source', '')
        texte_philo = exercise.get('texte', '')  # texte philosophique pour étude de texte

        total_questions = len(questions)
        exercise_ctx = f"Énoncé: {intro[:1000]}"
        if texte_philo:
            exercise_ctx += f"\n\nTexte philosophique:\n{texte_philo}"
        if questions:
            # Pass ALL questions so AI knows the full scope
            exercise_ctx += f"\n\nQuestions ({total_questions} au total):\n" + "\n".join(f"  {i+1}. {q}" for i, q in enumerate(questions))
        if solution:
            exercise_ctx += f"\n\nSolution complète (pour toi uniquement): {solution[:800]}"
        if hints:
            exercise_ctx += f"\n\nIndices disponibles: {'; '.join(str(h) for h in hints[:3])}"

        # Determine the language the AI must use based on subject
        _LANG_MAP = {
            'anglais':   'English',
            'espagnol':  'Spanish',
            'kreyol':    'Haitian Creole (Kreyòl Ayisyen)',
        }
        response_lang = _LANG_MAP.get(subject.lower(), 'French')
        lang_rule = (
            f"CRITICAL LANGUAGE RULE: You MUST reply ENTIRELY in {response_lang}. "
            f"Every single word you write must be in {response_lang}. "
            f"Do NOT mix languages. Do NOT use French if the subject is {subject}.\n"
        ) if subject.lower() in _LANG_MAP else (
            "RÈGLE DE LANGUE : Tu réponds toujours en français.\n"
        )

        # ── Prompt spécialisé philosophie ───────────────────────────────────
        _philo_type = exercise.get('_philo_type', '')
        if subject.lower() == 'philosophie' and _philo_type:
            _conseils = exercise.get('conseils', '')
            _solution = exercise.get('solution', '')
            if _philo_type == 'dissertation':
                system_prompt = (
                    f"Tu es Prof Bac — le meilleur prof de philosophie de {student_name} pour le BAC Haïti.\n"
                    f"RÈGLE DE LANGUE : Tu réponds toujours en français.\n\n"
                    f"Tu maîtrises parfaitement la méthodologie de la dissertation philosophique BAC Haïti :\n"
                    f"STRUCTURE OBLIGATOIRE :\n"
                    f"  • Introduction (6-12 lignes) : accroche → définitions des termes → problématique (formulée en QUESTION) → annonce du plan\n"
                    f"  • Thèse (15-20 lignes) : 2-3 arguments + exemples + auteurs + transition\n"
                    f"  • Antithèse (15-20 lignes) : 2-3 arguments qui nuancent + exemples + auteurs\n"
                    f"  • Conclusion (6-12 lignes) : synthèse + réponse à la problématique + ouverture\n\n"
                    f"INTERDIT (perte immédiate de points) :\n"
                    f"  ✗ 'Je pense que' → toujours 'On peut penser que', 'Il semble que'\n"
                    f"  ✗ Catalogue d'idées sans plan dialectique\n"
                    f"  ✗ Argument nouveau dans la conclusion\n"
                    f"  ✗ Absence de problématique formulée en question\n"
                    f"  ✗ Répéter la même idée en d'autres mots\n\n"
                    f"OBLIGATOIRE pour 10/10 :\n"
                    f"  ✓ Connecteurs logiques : 'D'abord', 'Ensuite', 'Cependant', 'En définitive'\n"
                    f"  ✓ Citer au moins 2 auteurs avec leur œuvre (Kant, Rousseau, Platon, Descartes, Spinoza, Marx, Freud, Aristote, Bergson, Pascal)\n"
                    f"  ✓ Plan dialectique équilibré (Thèse et Antithèse de longueur comparable)\n\n"
                    f"Critères de notation : {_solution}\n\n"
                    f"--- EXERCICE ---\n{exercise_ctx}\n--- FIN EXERCICE ---\n\n"
                    f"RÈGLES DE GUIDAGE ABSOLU :\n"
                    f"1. Tu guides question par question EN ORDRE (a → b → c → d)\n"
                    f"2. Tu ne donnes JAMAIS la réponse directement — tu poses des questions socratiques\n"
                    f"3. Si {student_name} fait une erreur (ex: manque la problématique, dit 'je pense'), tu CORRIGES immédiatement et lui expliques pourquoi c'est pénalisé\n"
                    f"4. Tu félicites UNIQUEMENT quand c'est vraiment correct\n"
                    f"5. Tu termines avec [NOTE:X/10] SEULEMENT quand les 4 parties sont rédigées\n"
                    f"6. Réponses concises : 3-5 phrases maximum par message\n"
                    f"7. Tu es strict mais bienveillant — comme un vrai correcteur du BAC Haïti\n"
                )
            else:  # étude de texte
                _phrase_q3 = exercise.get('_phrase_a_expliquer', '')
                system_prompt = (
                    f"Tu es Prof Bac — expert en philosophie pour le BAC Haïti de {student_name}.\n"
                    f"RÈGLE DE LANGUE : Tu réponds toujours en français.\n\n"
                    f"--- EXERCICE ---\n{exercise_ctx}\n--- FIN EXERCICE ---\n\n"
                    f"RÈGLES ABSOLUES :\n"
                    f"1. Tu travailles Q1 → Q2 → Q3 → Q4 EN ORDRE STRICT, une seule question à la fois.\n"
                    f"2. Tu commences par poser Q1 simplement et tu ATTENDS la réponse de {student_name} AVANT de continuer.\n"
                    f"3. Tu NE donnes JAMAIS d'indice, de méthode ou d'explication à l'avance — seulement sur demande explicite.\n"
                    f"4. Si {student_name} demande 'comment faire', 'je ne sais pas', 'aide-moi' ou échoue 2 fois sur la même question → là tu lui enseignes la méthode spécifique à cette question.\n"
                    f"   MÉTHODE Q1 (si demandée) : UNE phrase affirmative — 'L'auteur affirme/soutient/défend que...'. Interdit : résumé, question, citation.\n"
                    f"   MÉTHODE Q2 (si demandée) : 3-4 étapes logiques. Pour chaque étape : 'L'auteur [verbe fort] que...'. Verbes : affirme, distingue, oppose, enchaîne, conclut. Interdit : paraphraser phrase par phrase.\n"
                    f"   MÉTHODE Q3 (si demandée) : 5 étapes — (1) contexte, (2) mots-clés définis, (3) sens philosophique, (4) exemple concret, (5) enjeu philosophique. Interdit : paraphraser.\n"
                    f"   MÉTHODE Q4 (si demandée) : 'L'intérêt philosophique du texte réside dans le fait que [auteur] nous invite à réfléchir sur [problème]. [3 questions]. L'ensemble de ces interrogations constituent l'intérêt philosophique du texte.'\n"
                    f"5. Si la réponse est incorrecte → corriger directement ('Attention, ce n'est pas tout à fait ça...') et guider sans donner la réponse.\n"
                    f"6. Si la réponse est correcte → valider brièvement et passer à la question suivante.\n"
                    f"7. Tu termines avec [NOTE:X/10] SEULEMENT quand les 4 questions sont toutes traitées.\n"
                    f"8. Réponses concises : 2-4 phrases max par message.\n"
                    f"Phrase à expliquer pour Q3 : \"{_phrase_q3}\"\n"
                )
        else:
            # ── Prompt générique (autres matières) ──────────────────────────
            system_prompt = (
            f"You are Prof Bac — a rigorous tutor for the Haitian BAC exam.\n"
            f"{lang_rule}"
            f"You teach {subject} in a clear, simple, and academic way for BAC preparation.\n"
            f"ABSOLUTE RULES:\n"
            f"1. NEVER give the final answer directly. Guide with simple steps.\n"
            f"2. If the student doesn't understand, restate the definition first, then break it into small steps.\n"
            f"3. Use neutral pedagogical language. Avoid slang, exaggeration, or theatrical metaphors.\n"
            f"4. ACCURACY IS SACRED: If the student's answer is WRONG or imprecise, you MUST correct it clearly — never agree with a wrong answer, never say 'Excellent' or 'Tu as raison' if the answer is incorrect. Say 'Pas tout à fait...' or 'Attention...' and explain why.\n"
            f"5. Praise ONLY genuinely correct reasoning: 'Excellent!', 'Good thinking!', 'Almost there!' — only when the answer is actually right.\n"
            f"6. Guide question by question, IN ORDER (Q1 first, then Q2, then Q3, etc.).\n"
            f"7. ABOUT ENDING SESSION: The exercise has {total_questions} question(s) total. "
            f"You ONLY end the session (with [NOTE:X/10]) when the student has answered ALL {total_questions} questions. "
            f"If only some questions are done, continue with the next one.\n"
            f"8. When ALL {total_questions} questions are completed → give a score out of 10 with [NOTE:X/10] at the end.\n"
            f"9. Reply in 3-5 sentences maximum per message. Concise and clear.\n"
            f"10. Math formulas in LaTeX inline: $F = ma$.\n\n"
            f"--- EXERCISE ---\n{exercise_ctx}\n--- END EXERCISE ---\n\n"
            f"Source: {source}\n"
            f"Remember: you are a real teacher who truly wants {student_name} to succeed. "
            f"The exercise has {total_questions} questions — do not end before covering all of them!"
        )

        # When student clicks "Indice", override prompt for a precise hint with formula
        is_hint = user_message.startswith('[HINT]')
        if is_hint:
            system_prompt = (
                f"Tu es Prof Bac — tuteur expert en {subject} pour {student_name}.\n"
                f"{lang_rule}"
                f"--- EXERCICE ---\n{exercise_ctx}\n--- FIN EXERCICE ---\n\n"
                f"MISSION INDICE PRÉCIS :\n"
                f"1. Identifie LA formule, loi ou théorème CLÉS qui permet de résoudre la question en cours.\n"
                f"2. Cite cette formule en LaTeX : $formule$. Ex: $P(R) = P(R|U_1)P(U_1) + P(R|U_2)P(U_2) + P(R|U_3)P(U_3)$\n"
                f"3. Explique en 1-2 phrases COMMENT appliquer cette formule à CET exercice précis.\n"
                f"4. Ne donne PAS la valeur numérique finale. Juste l'outil et comment l'utiliser.\n"
                f"5. INTERDIT : commencer par '??', '?', ou des points d'interrogation. Commence directement par 'La formule à utiliser est...' ou 'Pour cette question, applique...'.\n"
                f"6. 3-4 phrases maximum."
            )

        # When the student clicks "Terminer", override the system prompt so the AI
        # ALWAYS gives a final note — regardless of how many questions were answered.
        is_finish = user_message.startswith('[FINISH]')
        if is_finish:
            done_pct = len([m for m in messages if m.get('role') == 'user']) / max(1, total_questions)
            system_prompt = (
                f"Tu es Prof Bac, correcteur bienveillant de {student_name}."
                f" L'élève vient de terminer sa session sur cet exercice en {subject}."
                f" Voici l'exercice : {exercise_ctx}"
                f" Ta mission:\n"
                f" 1. Fais un bilan honnête en 2-3 phrases de ce que l'élève a fait (bien ou mal,"
                f" même si la session est incomplète).\n"
                f" 2. Donne OBLIGATOIREMENT une note sur 10 avec la balise exacte [NOTE:X/10] à la fin"
                f" (ex: [NOTE:5/10]). Ne saute PAS cette balise.\n"
                f" 3. Si la session est incomplète, tiens compte de l'effort fourni mais sois juste.\n"
                f" RÈGLE ABSOLUE : tu dois TOUJOURS terminer ton message avec [NOTE:X/10]. Jamais d'exception."
            )
            user_message = 'Donne le bilan et la note finale de ma session.'

        # Build conversation for AI
        # Rolling summary: last 8 verbatim + compact memory of older exchanges
        from . import gemini as _gemini
        hist_summary, recent_msgs = _gemini._build_compact_history(messages, keep=8)
        ai_messages = [{"role": "system", "content": system_prompt}]
        if hist_summary:
            ai_messages.append({"role": "system", "content": hist_summary})
        for msg in recent_msgs:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            if role in ('user', 'assistant') and content:
                ai_messages.append({"role": role, "content": content[:500]})
        # Final user message — with optional image
        if _ex_image_data:
            import base64 as _b64mod2
            _b64_ex = _b64mod2.b64encode(_ex_image_data).decode('utf-8')
            ai_messages.append({"role": "user", "content": [
                {"type": "text", "text": user_message},
                {"type": "image_url", "image_url": {"url": f"data:{_ex_image_mime};base64,{_b64_ex}"}}
            ]})
        else:
            ai_messages.append({"role": "user", "content": user_message})

        # Call AI — use VISION_MODEL when image is attached
        from . import gemini as _gemini
        _ex_model = _gemini.VISION_MODEL if _ex_image_data else _gemini.FAST_MODEL
        resp = _gemini._client().chat.completions.create(
            model=_ex_model,
            messages=ai_messages,
            max_tokens=400,
        )
        response = resp.choices[0].message.content or ''
        # Clean markdown
        import re as _re
        response = _re.sub(r'\*\*(.+?)\*\*', r'\1', response)
        response = _re.sub(r'\*(.+?)\*', r'\1', response)

        return JsonResponse({'ok': True, 'response': response.strip()})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


# ─────────────────────────────────────────────
# EXERCISE COMPLETION — XP update
# ─────────────────────────────────────────────
@login_required
@require_POST
def api_exercise_complete(request):
    """
    Called when a chat-guided exercise session ends with a score.
    POST: {score: 0-10, subject: str}
    Increments exercices_resolus and minutes_etude, returns new XP.
    """
    try:
        data = json.loads(request.body)
        score = float(data.get('score', 0))
        score = max(0.0, min(10.0, score))  # clamp 0-10

        stats = _get_or_create_stats(request.user)
        stats.exercices_resolus += 1
        # Scale study time: 10/10 = 20 min, 5/10 = 10 min, 0/10 = 5 min
        minutes = max(5, round(score * 2))
        stats.minutes_etude += minutes
        stats.save(update_fields=['exercices_resolus', 'minutes_etude'])

        _update_streak(request.user)

        def calc_xp(s):
            return s.quiz_completes * 20 + s.exercices_resolus * 50 + s.messages_envoyes * 5

        new_xp = calc_xp(stats)
        return JsonResponse({'ok': True, 'xp': new_xp, 'exercices_resolus': stats.exercices_resolus})
    except Exception as e:
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


# ─────────────────────────────────────────────
# BIBLIOTHÈQUE PDF
# ─────────────────────────────────────────────
def library_view(request):
    """Liste tous les examens PDF organisés par matière."""
    is_guest_user = _is_guest(request)
    if not request.user.is_authenticated and not is_guest_user:
        return redirect('/login/?next=' + request.get_full_path())
    _SUBJ_DIR_MAP = {
        'maths':       'examens_maths',
        'physique':    'examens_physique',
        'chimie':      'examens_chimie',
        'svt':         'examens_svt',
        'francais':    'examens_francais',
        'anglais':     'examens_anglais',
        'espagnol':    'examens_espagnol',
        'philosophie': 'examens_philosophie',
        'histoire':    'examens_histoire',
        'economie':    'examens_economie',
        'informatique':'examens_informatique',
        'art':         'examens_art',
    }
    db_root = os.path.join(settings.BASE_DIR, 'database')
    library = []
    for subj, dirname in _SUBJ_DIR_MAP.items():
        mat_info = MATS.get(subj, {})
        folder = os.path.join(db_root, dirname)
        if not os.path.isdir(folder):
            continue
        files = []
        for fname in sorted(os.listdir(folder)):
            if not fname.lower().endswith('.pdf'):
                continue
            fpath = os.path.join(folder, fname)
            size_kb = os.path.getsize(fpath) // 1024
            yr_m = re.search(r'20\d{2}', fname)
            year = yr_m.group(0) if yr_m else '—'
            display = fname.replace(f'exam_{subj}_', '').replace(f'exam_{dirname}_', '')
            display = display.replace('.pdf', '').replace('_', ' ').replace('-', ' ').strip()
            files.append({
                'name': display,
                'fname': fname,
                'year': year,
                'size_kb': size_kb,
                'rel_path': f'{dirname}/{fname}',
            })
        if files:
            library.append({
                'subject': subj,
                'label': mat_info.get('label', subj),
                'color': mat_info.get('color', '#10B981'),
                'icon': mat_info.get('icon', 'fa-file-pdf'),
                'files': files,
                'count': len(files),
            })

    # Plus de filtrage par sujet - afficher tous les exams de toutes les matières
    filter_subj = 'all'  # Forcer à 'all' pour ne plus filtrer

    # Check premium for gating library actions
    user_is_premium = False
    if request.user.is_authenticated:
        from core.premium import is_premium as _is_prem
        user_is_premium = _is_prem(request.user)

    return render(request, 'core/library.html', {
        'library': library,
        'mats': MATS,
        'filter_subj': filter_subj,
        'is_guest': is_guest_user,
        'is_premium': user_is_premium,
    })


@login_required
def api_pdf_serve(request):
    """Sert un PDF de la base de données (téléchargement sécurisé)."""
    # ── Premium gate: PDF actions bloquées pour non-premium ──
    if request.user.is_authenticated:
        from core.premium import is_premium, premium_required_json
        if not is_premium(request.user):
            return JsonResponse(premium_required_json(), status=403)

    from django.http import FileResponse, Http404
    rel = request.GET.get('path', '').strip()
    if not rel or '..' in rel or not re.match(r'^(examens_\w+|chapter)/[\w\s\-\.]+\.pdf$', rel, re.IGNORECASE):
        raise Http404
    fpath = os.path.join(settings.BASE_DIR, 'database', rel)
    if not os.path.isfile(fpath):
        raise Http404
    return FileResponse(open(fpath, 'rb'), content_type='application/pdf',
                        as_attachment=request.GET.get('dl') == '1',
                        filename=os.path.basename(fpath))


@login_required
def api_pdf_extract_text(request):
    """Extrait le texte d'un PDF de la bibliothèque via pdfplumber — accessible à tous les utilisateurs connectés."""
    rel = request.GET.get('path', '').strip()
    if not rel or '..' in rel or not re.match(r'^(examens_\w+|chapter)/[\w\s\-\.]+\.pdf$', rel, re.IGNORECASE):
        return JsonResponse({'error': 'Chemin invalide'}, status=400)
    fpath = os.path.join(settings.BASE_DIR, 'database', rel)
    if not os.path.isfile(fpath):
        return JsonResponse({'error': 'Fichier introuvable'}, status=404)
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(fpath) as pdf:
            for page in pdf.pages[:60]:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        full_text = '\n'.join(text_parts).strip()
        if not full_text:
            return JsonResponse({'error': 'Aucun texte extractible dans ce PDF'}, status=422)
        return JsonResponse({'text': full_text[:12000], 'name': os.path.basename(fpath)})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ═════════════════════════════════════════════════════════════════════════════
# QUIZ DUEL — MODE DÉFI EN LIGNE (polling-based, no WebSockets needed)
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_duel_questions(subject: str, count: int = 10) -> list:
    """
    Construit un pool de questions QCM pour un duel.
    Réutilise le pipeline existant : JSON pré-exporté → enrichissement → IA.
    Retourne une liste de dicts prêts à l'emploi.
    """
    questions = []

    # ── SPECIAL: Kreyòl — 100% depuis quiz_kreyol.json, aucune IA ──────────────
    if subject == 'francais':
        from pathlib import Path as _krdPath
        _krd_file = _krdPath(__file__).resolve().parent.parent / 'database' / 'quiz_kreyol.json'
        try:
            import json as _krdj
            _krd_data = _krdj.loads(_krd_file.read_text(encoding='utf-8'))
            _krd_qs = _krd_data.get('quiz', [])
            if _krd_qs:
                import random as _krdrnd
                _krdrnd.shuffle(_krd_qs)
                for _kq in _krd_qs[:count]:
                    _kopts = list(_kq.get('options', []))
                    _kcl   = _kq.get('correct', 'A').upper()
                    _kci   = {'A':0,'B':1,'C':2,'D':3}.get(_kcl, 0)
                    _kans  = _kopts[_kci] if _kci < len(_kopts) else ''
                    _krdrnd.shuffle(_kopts)
                    try:
                        _krc = _kopts.index(_kans)
                    except ValueError:
                        _krc = 0
                    questions.append({
                        'enonce':           _kq.get('question',''),
                        'options':          _kopts,
                        'reponse_correcte': _krc,
                        'explication':      _kq.get('explanation', _kq.get('explication','')),
                        'theme':            _kq.get('category','Kreyòl'),
                        'difficulte':       _kq.get('difficulty','moyen'),
                        'source':           'quiz_kreyol',
                        'type':             'qcm',
                    })
        except Exception:
            import traceback; traceback.print_exc()
        random.shuffle(questions)
        return questions[:count]

    # ── SPECIAL: Sujets avec fichier JSON hand-crafted (duel) ─────────────────
    _DUEL_JSON_FILES = {
        'svt':          'quiz_SVT.json',
        'histoire':     'quiz_sc_social.json',
        'physique':     'quiz_physique.json',
        'philosophie':  'quiz_philosophie.json',
        'informatique': 'quiz_informatique.json',
        'economie':     'quiz_economie.json',
        'chimie':       'quiz_chimie.json',
        'art':          'quiz_art.json',
    }
    if subject in _DUEL_JSON_FILES:
        from pathlib import Path as _djPath
        import json as _djj
        _dj_file = _djPath(__file__).resolve().parent.parent / 'database' / _DUEL_JSON_FILES[subject]
        try:
            _dj_data = _djj.loads(_dj_file.read_text(encoding='utf-8'))
            _dj_qs   = _dj_data.get('quiz', [])
            random.shuffle(_dj_qs)
            for _q in _dj_qs[:count]:
                # Re-shuffle options at serve time for full unpredictability
                _opts = list(_q.get('options', []))
                _correct_letter = _q.get('correct', 'A').upper()
                _correct_idx = {'A':0,'B':1,'C':2,'D':3}.get(_correct_letter, 0)
                _answer_text = _opts[_correct_idx] if _correct_idx < len(_opts) else ''
                random.shuffle(_opts)
                try:
                    _rc = _opts.index(_answer_text)
                except ValueError:
                    _rc = 0
                questions.append({
                    'enonce':           _q.get('question', _q.get('enonce', '')),
                    'options':          _opts,
                    'reponse_correcte': _rc,
                    'explication':      _q.get('explanation', _q.get('explication', '')),
                    'theme':            _q.get('category', subject),
                    'difficulte':       _q.get('difficulty', _q.get('difficulte', 'moyen')),
                    'source':           f'quiz_{subject}_json',
                })
        except Exception:
            import traceback; traceback.print_exc()
        random.shuffle(questions)
        return questions[:count]

    # ── SPECIAL: Anglais & Espagnol — 100% AI chapter-based (no JSON pool) ──
    if subject in ('anglais', 'espagnol'):
        ai_qs = gemini.generate_quiz_questions(subject, count=count)
        for q in (ai_qs or []):
            opts = q.get('options', [])
            if len(opts) < 4:
                continue
            questions.append({
                'enonce':           q.get('enonce', '').strip(),
                'options':          opts[:4],
                'reponse_correcte': q.get('reponse_correcte', 0),
                'explication':      q.get('explication', ''),
                'theme':            q.get('theme', q.get('sujet', '')),
                'difficulte':       q.get('difficulte', 'moyen'),
                'source':           q.get('source', f'ai_{subject}'),
            })
        return questions

    # ── 1. Pool JSON reconstruit ─────────────────────────────────────────
    pool = pdf_loader.get_quiz_items_pool(subject, size=60)
    if pool:
        approved = _local_filter_quiz_pool(pool, subject, wanted=count * 3)
        for item in approved:
            itype = item.get('type', '')
            opts  = item.get('options', [])
            if itype == 'qcm' and len(opts) >= 4 and not all(
                o.strip().upper() in ('VRAI', 'FAUX', 'TRUE', 'FALSE') for o in opts
            ):
                rc = item.get('reponse_correcte', 0)
                if isinstance(rc, str):
                    labels = ['A', 'B', 'C', 'D', 'E']
                    rc = labels.index(rc.strip().upper()) if rc.strip().upper() in labels else 0
                try:
                    rc = int(rc)
                except (ValueError, TypeError):
                    rc = 0
                questions.append({
                    'enonce':           item.get('enonce', '').strip(),
                    'options':          opts[:4],
                    'reponse_correcte': rc,
                    'explication':      item.get('explication', ''),
                    'theme':            item.get('theme', ''),
                    'difficulte':       item.get('difficulte', 'moyen'),
                    'source':           item.get('source', ''),
                })
            if len(questions) >= count:
                break

    # ── 2. Enrichissement open-questions (histoire/economie) ────
    # NB: espagnol utilise sa propre génération chapitre-par-chapitre (comme anglais).
    if len(questions) < count and pool and subject in ('histoire', 'economie'):
        open_pool = [it for it in pool if it.get('type') == 'question' and not it.get('options')]
        if open_pool:
            need = count - len(questions)
            enriched = gemini.enrich_open_questions_to_qcm(open_pool, subject, count=need * 2)
            for q in enriched:
                questions.append({
                    'enonce':           q.get('enonce', '').strip(),
                    'options':          q.get('options', [])[:4],
                    'reponse_correcte': q.get('reponse_correcte', 0),
                    'explication':      q.get('explication', ''),
                    'theme':            q.get('theme', ''),
                    'difficulte':       q.get('difficulte', 'moyen'),
                    'source':           q.get('source', ''),
                })
                if len(questions) >= count:
                    break

    # ── 3. Fallback IA directe ───────────────────────────────────────────
    if len(questions) < count:
        need = count - len(questions)
        ai_qs = gemini.generate_quiz_questions(subject, count=need)
        for q in (ai_qs or []):
            opts = q.get('options', [])
            rc   = q.get('reponse_correcte', 0)
            try:
                rc = int(rc)
            except (ValueError, TypeError):
                rc = 0
            questions.append({
                'enonce':           q.get('enonce', '').strip(),
                'options':          opts[:4],
                'reponse_correcte': rc,
                'explication':      q.get('explication', ''),
                'theme':            q.get('theme', ''),
                'difficulte':       q.get('difficulte', 'moyen'),
                'source':           'ai',
            })

    random.shuffle(questions)
    return questions[:count]


@login_required
def duel_view(request):
    """Page principale du mode Duel."""
    return render(request, 'core/duel.html', {'mats': MATS})


@login_required
@require_POST
def api_duel_create(request):
    """Crée une nouvelle session de duel et génère les questions partagées."""
    from .models import QuizDuel
    from .models import QuizQuestion
    from django.utils import timezone as _tz
    from datetime import timedelta

    try:
        data    = json.loads(request.body)
        subject = data.get('subject', 'maths').strip()
        count   = max(5, min(15, int(data.get('count', 10))))
    except Exception:
        return JsonResponse({'error': 'Données invalides'}, status=400)

    if subject not in MATS:
        return JsonResponse({'error': 'Matière invalide'}, status=400)

    expires = _tz.now() + timedelta(minutes=15)
    code    = QuizDuel.generate_code()

    questions = _fetch_duel_questions(subject, count=count)
    if not questions:
        # Fallback sans IA: pioche dans la table QuizQuestion si elle est peuplée.
        try:
            db_qs = list(QuizQuestion.objects.filter(subject=subject).order_by('?')[:count])
            questions = [q.to_dict() for q in db_qs]
        except Exception:
            questions = []
        if not questions:
            return JsonResponse({'error': 'Impossible de charger les questions. Réessaie.'}, status=500)

    duel = QuizDuel.objects.create(
        code       = code,
        creator    = request.user,
        subject    = subject,
        questions  = questions,
        expires_at = expires,
        status     = 'waiting',
    )
    return JsonResponse({
        'code':       duel.code,
        'subject':    subject,
        'total':      len(questions),
        'expires_in': 900,
    })


@login_required
@require_POST
def api_duel_join(request):
    """Rejoint un duel existant via son code."""
    from .models import QuizDuel
    from django.utils import timezone as _tz

    try:
        data = json.loads(request.body)
        code = data.get('code', '').strip().upper()
    except Exception:
        return JsonResponse({'error': 'Données invalides'}, status=400)

    if not code:
        return JsonResponse({'error': 'Code requis'}, status=400)

    try:
        duel = QuizDuel.objects.get(code=code)
    except QuizDuel.DoesNotExist:
        return JsonResponse({'error': 'Code introuvable. Vérifie le code et réessaie.'}, status=404)

    if duel.is_expired():
        duel.status = 'expired'
        duel.save(update_fields=['status'])
        return JsonResponse({'error': 'Ce duel a expiré.'}, status=410)

    if duel.status != 'waiting':
        return JsonResponse({'error': 'Ce duel est déjà en cours ou terminé.'}, status=409)

    if duel.creator == request.user:
        return JsonResponse({'error': 'Tu ne peux pas défier toi-même !'}, status=400)

    duel.challenger = request.user
    duel.status     = 'active'
    duel.save(update_fields=['challenger', 'status'])

    return JsonResponse({
        'code':    duel.code,
        'subject': duel.subject,
        'total':   len(duel.questions),
        'creator': duel.creator.get_full_name() or duel.creator.username,
    })


@login_required
def api_duel_state(request):
    """Polling endpoint — retourne l'état courant du duel (appelé toutes les 2-3 s)."""
    from .models import QuizDuel

    code = request.GET.get('code', '').strip().upper()
    if not code:
        return JsonResponse({'error': 'Code requis'}, status=400)

    try:
        duel = QuizDuel.objects.get(code=code)
    except QuizDuel.DoesNotExist:
        return JsonResponse({'error': 'Duel introuvable'}, status=404)

    is_creator    = (duel.creator == request.user)
    is_challenger = (duel.challenger == request.user)
    if not is_creator and not is_challenger:
        return JsonResponse({'error': 'Accès refusé'}, status=403)

    if duel.status == 'waiting' and duel.is_expired():
        duel.status = 'expired'
        duel.save(update_fields=['status'])

    my_score      = duel.creator_score    if is_creator else duel.challenger_score
    opp_score     = duel.challenger_score if is_creator else duel.creator_score
    my_finished   = duel.creator_finished    if is_creator else duel.challenger_finished
    opp_finished  = duel.challenger_finished if is_creator else duel.creator_finished

    opponent_name = ''
    if is_creator and duel.challenger:
        opponent_name = duel.challenger.get_full_name() or duel.challenger.username
    elif is_challenger:
        opponent_name = duel.creator.get_full_name() or duel.creator.username

    return JsonResponse({
        'status':        duel.status,
        'my_score':      my_score,
        'opp_score':     opp_score,
        'my_finished':   my_finished,
        'opp_finished':  opp_finished,
        'opponent_name': opponent_name,
        'total':         len(duel.questions),
        'subject':       duel.subject,
        'questions':     duel.questions if duel.status == 'active' else [],
    })


@login_required
@require_POST
def api_duel_finish(request):
    """Soumet les réponses finales et met à jour le score du joueur."""
    from .models import QuizDuel

    try:
        data    = json.loads(request.body)
        code    = data.get('code', '').strip().upper()
        answers = data.get('answers', [])
        score   = int(data.get('score', 0))
    except Exception:
        return JsonResponse({'error': 'Données invalides'}, status=400)

    try:
        duel = QuizDuel.objects.get(code=code)
    except QuizDuel.DoesNotExist:
        return JsonResponse({'error': 'Duel introuvable'}, status=404)

    is_creator    = (duel.creator == request.user)
    is_challenger = (duel.challenger == request.user)
    if not is_creator and not is_challenger:
        return JsonResponse({'error': 'Accès refusé'}, status=403)

    if duel.status not in ('active', 'finished'):
        return JsonResponse({'error': "Le duel n'est pas actif"}, status=409)

    if is_creator and not duel.creator_finished:
        duel.creator_answers  = answers
        duel.creator_score    = score
        duel.creator_finished = True
    elif is_challenger and not duel.challenger_finished:
        duel.challenger_answers  = answers
        duel.challenger_score    = score
        duel.challenger_finished = True

    if duel.creator_finished and duel.challenger_finished:
        duel.status = 'finished'

    duel.save()
    return JsonResponse({'ok': True, 'status': duel.status})
