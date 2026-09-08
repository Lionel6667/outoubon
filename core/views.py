import json
import os
import random
import re
import hashlib
import logging
import functools
import unicodedata
from datetime import date
from pathlib import Path

from django.conf import settings

from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET

from django.db import models, transaction
from accounts.models import DiagnosticResult, UserProfile
from django.contrib.contenttypes.models import ContentType
from accounts.models import MessageDeletion

from .models import (
    QuizQuestion, QuizSession, ChatMessage, UserStats,
    Flashcard, FlashcardProgress, RevisionPlan, QuizAnalysis, BookmarkedQuestion,
    SubjectChapter, CourseSession, CourseProgressState, GeneratedCourseAsset,
    MistakeTracker, SubjectMastery, ChatSessionSummary, LearningEvent,
    ExtraBetPost, ExtraBetAttempt, GeneratedExam, UserSeenExamItem,
    ExerciseSession,
)
from . import gemini
from . import pdf_loader
from . import local_responses
from .series_data import get_priority_subjects, get_serie_context_text, SERIES
from .exercise_generator import generate_physics_exercise
from django.utils import timezone as _timezone

def _local_time(dt):
    """Convert a UTC datetime to local time (America/Port-au-Prince)."""
    if dt is None:
        return None
    try:
        return _timezone.localtime(dt)
    except Exception:
        return dt

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

# Hard caps — coût tokens (entrée)
AI_BLOCK_MAX_OUTPUT_CHARS = 3_500
AI_BLOCK_MAX_OUTPUT_COURSE = 3_000
AI_CHAT_CONTEXT_MAX_CHARS = 2_800   # chat — cap entrée (voir chat_token_optimizer)
CHAT_HISTORY_DB_LOAD = 4            # fenêtre historique chat (2 échanges)
CHAT_HISTORY_KEEP = 6           # messages verbatim max (3 échanges user/assistant)
COURSE_SESSION_CHAT_KEEP = 6    # session cours — sliding window ultra-strict

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

        type_bonus = {'exercise': 200, 'examples': 60, 'detailed_examples': 30}.get(btype, 0)
        bac_bonus = 20 if ('bac ' in search_zone or '(bac' in search_zone) else 0
        content_len = len((b.get('content', '') or '').strip())
        score = (exact * 100) + type_bonus + bac_bonus + min(content_len, 1800) / 30

        if score > best_score:
            best_score = score
            best = b

    return best


def _search_ai_blocks(
    subject: str,
    chapter_num: int,
    query: str,
    max_blocks: int = 12,
    max_output_chars: int = AI_BLOCK_MAX_OUTPUT_CHARS,
    summary_only: bool = False,
    return_top_score: bool = False,
) -> str | tuple[str, float]:
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
        # Chat global : limiter aux résumés pour éviter de scanner toute la matière.
        if summary_only:
            chapter_blocks = [
                b for b in all_blocks
                if b.get('type') in ('chapter_summary', 'summary')
            ] or all_blocks[:]
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

    # Format as context text (hard cap on total output size)
    if not selected:
        return ''

    lines = []
    prev_chapter = None
    prev_sub = None
    total_out = 0

    for block in selected:
        chapter = block.get('chapter', '')
        sub = block.get('subchapter', '')
        btype = block.get('type', '')
        content = block.get('content', '').strip()

        if not content:
            continue

        if chapter != prev_chapter:
            header = f"\n## {chapter}"
            if total_out + len(header) > max_output_chars:
                break
            lines.append(header)
            total_out += len(header)
            prev_chapter = chapter
            prev_sub = None

        if sub != prev_sub:
            header = f"\n### {sub}"
            if total_out + len(header) > max_output_chars:
                break
            lines.append(header)
            total_out += len(header)
            prev_sub = sub

        _TYPE_LABELS = {
            'chapter_summary':  'Résumé du chapitre',
            'definition':       'Définition',
            'explanation':      'Explication',
            'method':           'Méthode',
            'examples':         'Exemples',
            'detailed_examples':'Exemples détaillés',
            'methods':          'Méthodes',
            'common_mistakes':  'Erreurs fréquentes',
            'summary':          'Synthèse',
            'exercise':         'Exercice',
        }
        label = _TYPE_LABELS.get(btype, btype.replace('_', ' ').capitalize())
        remaining = max_output_chars - total_out
        if remaining <= 80:
            break
        snippet = content if len(content) <= remaining - 40 else content[: remaining - 40].rstrip() + '…'
        block_text = f"\n**{label}**\n{snippet}\n"
        lines.append(block_text)
        total_out += len(block_text)

    formatted = '\n'.join(lines) if lines else ''
    if return_top_score:
        return formatted, float(top_score)
    return formatted


def _build_course_ai_context(subject: str, chapter_num: int, user_msg: str, max_chars: int = AI_BLOCK_MAX_OUTPUT_COURSE) -> tuple[str, str]:
    """
    Contexte IA pour le cours — extraits STABLES par chapitre (cache DeepSeek).
    user_msg est ignoré exprès : un extrait qui change à chaque question casse le prefix cache.
    """
    full = pdf_loader.get_note_chapter_content(subject, chapter_num)
    if full:
        return full[:max_chars], 'notes_stable'

    excerpt = pdf_loader.get_note_chapter_ai_context(
        subject, chapter_num, max_chars=max_chars, query='',
    )
    if excerpt:
        return excerpt[:max_chars], 'notes_stable'

    summary = pdf_loader.get_chapter_summary_context(subject, chapter_num, max_chars=max_chars)
    if summary:
        return summary[:max_chars], 'chapter_summary'

    return '', 'empty'


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
            f"Un solenoide de {n} spires, de longueur ${length}\\,m$ et de section ${section_area:.2e}\\,m^2$, "
            f"est parcouru par un courant continu de ${current}\\,A$. On l etudie en s inspirant du sujet {source_ref}."
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
            f"Une tige conductrice de longueur ${length}\\,m$ parcourue par un courant de ${current}\\,A$ est placee dans un champ uniforme "
            f"de ${b}\\,T$, perpendiculairement aux lignes de champ. Situation inspiree de {source_ref}."
        )
        questions = [
            "Determine la valeur de la force de Laplace exercee sur la tige.",
            "Precise le sens de la force en utilisant la regle des trois doigts.",
            "Calcule le travail de cette force si la tige se deplace de $0,12\\,m$ dans son sens.",
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
            f"Un galvanometre a cadre mobile comporte {n} spires de surface ${area:.2e}\\,m^2$ dans un champ radial de ${b}\\,T$. "
            f"La constante de torsion vaut ${k:.2e}\\,N\\cdot m/rad$. Situation inspiree de {source_ref}."
        )
        questions = [
            "Etablis la relation entre la deviation $\\theta$ et le courant $I$.",
            "Calcule la sensibilite du galvanometre.",
            "Determine la deviation pour un courant de $2\\,mA$.",
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
            "Determine l intensite efficace du courant si la tension efficace vaut $120\\,V$.",
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
    """Retourne l'ensemble des clés de matières pour la série du user, filtré par langue étrangère."""
    try:
        profile = user.profile
        serie = profile.serie or 'SVT'
    except Exception:
        serie = 'SVT'
    subjs = set(SERIES.get(serie, SERIES['SVT'])['subjects'].keys())
    # Kreyol doit rester visible hors Examen Blanc.
    subjs.add('francais')
    # Filtrer selon la langue étrangère choisie
    try:
        langue = user.profile.langue_etrangere or 'anglais'
    except Exception:
        langue = 'anglais'
    if langue == 'espagnol':
        subjs.discard('anglais')
        subjs.add('espagnol')
    else:  # anglais par défaut
        subjs.discard('espagnol')
        subjs.add('anglais')
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

def _get_subj_label(subj):
    """Label d'affichage. En NS4, francais = Kreyòl — jamais « Français »."""
    if not subj:
        return ''
    key = str(subj).strip().lower()
    aliases = {
        'kreyol': 'francais',
        'kreyòl': 'francais',
        'creole': 'francais',
        'créole': 'francais',
        'français': 'francais',
        'francais': 'francais',
    }
    key = aliases.get(key, key)
    label = MATS.get(key, {}).get('label')
    if label:
        return label
    raw = str(subj).strip()
    low = raw.lower()
    if 'français' in low and 'krey' not in low:
        return 'Kreyòl'
    return raw


def _format_chat_preview_text(content: str, max_len: int = 80) -> str:
    """Texte court pour titres d'historique (sans PDF brut ni sauts de ligne)."""
    if not content:
        return ''
    s = str(content).strip()
    s = re.sub(r'📄\s*\*\*PDF:.*', '', s, flags=re.I | re.S)
    s = re.sub(r'Analyse et aide-moi à réviser[^.]*\.?\s*', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + '…'
    return s


def _usable_chat_title(val) -> str:
    if not isinstance(val, str):
        return ''
    t = val.strip()
    if not t or len(t) >= 90 or len(t.split()) > 8:
        return ''
    from core.chat_title import is_weak_title
    if is_weak_title(t):
        return ''
    return t


def _chat_conversation_title(first_content: str, subject: str, summary_row=None, extra_user_msgs=None) -> str:
    """Titre lisible : mots-clés heuristiques, ou titre court déjà stocké."""
    if summary_row and summary_row.summary:
        summ = summary_row.summary
        if isinstance(summ, dict):
            for key in ('title', 'topic', 'main_topic'):
                stored = _usable_chat_title(summ.get(key))
                if stored:
                    return stored
    from core.chat_title import conversation_title_from_thread
    msgs = []
    if first_content:
        msgs.append(first_content)
    for extra in extra_user_msgs or []:
        if extra:
            msgs.append(extra)
    return conversation_title_from_thread(msgs, subject)


def _persist_and_return_chat_title(user, session_key: str, text: str, subject: str) -> str:
    try:
        from core.chat_title import persist_conversation_title
        return persist_conversation_title(user, session_key, text, subject)
    except Exception:
        from core.chat_title import conversation_title_from_thread
        return conversation_title_from_thread([text], subject)


def _chat_greeting_for(user) -> str:
    from django.utils import timezone as _tz
    hour = _tz.localtime().hour
    first = (getattr(user, 'first_name', '') or getattr(user, 'username', '') or 'Élève').strip()
    salut = 'bonsoir' if (hour >= 18 or hour < 5) else 'bonjour'
    icon = ' 🌙' if salut == 'bonsoir' else ''
    return f'{first}, {salut} !{icon}'


def _list_chat_conversations(user, q: str = '', limit: int = 500):
    from django.db.models import Max, Count
    from collections import defaultdict

    q = (q or '').strip()
    base = ChatMessage.objects.filter(user=user).exclude(session_key='')
    if q:
        keys = (
            ChatMessage.objects.filter(user=user, content__icontains=q)
            .exclude(session_key='')
            .values_list('session_key', flat=True)
            .distinct()
        )
        base = base.filter(session_key__in=keys)
    conversations = list(
        base.values('session_key', 'subject')
        .annotate(last_msg=Max('created_at'), msg_count=Count('id'))
        .order_by('-last_msg')[:limit]
    )
    session_keys = [c['session_key'] for c in conversations]
    user_msgs_map = defaultdict(list)
    if session_keys:
        for m in (
            ChatMessage.objects.filter(user=user, session_key__in=session_keys, role='user')
            .order_by('created_at')
            .only('session_key', 'content')
        ):
            bucket = user_msgs_map[m.session_key]
            if len(bucket) < 6:
                bucket.append(m.content)
    summary_map = {
        s.session_key: s
        for s in ChatSessionSummary.objects.filter(user=user, session_key__in=session_keys)
    } if session_keys else {}
    out = []
    for c in conversations:
        sk = c['session_key']
        msgs = user_msgs_map.get(sk) or []
        first_raw = msgs[0] if msgs else ''
        extra = msgs[1:]
        out.append({
            'session_key': sk,
            'subject':     c['subject'],
            'label':       _get_subj_label(c['subject']),
            'last_msg':    c['last_msg'],
            'msg_count':   c['msg_count'],
            'preview':     _chat_conversation_title(first_raw, c['subject'], summary_map.get(sk), extra),
        })
    return out


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
    try:
        from datetime import timedelta as _td
        from core.xp import apply_streak_xp
        started = today - _td(days=max(0, profile.streak - 1))
        apply_streak_xp(user, profile.streak, started)
    except Exception:
        pass
    if profile.streak in (7, 14, 30, 60, 100):
        from core.push_events import push_streak_milestone
        push_streak_milestone(user, profile.streak)
    return profile.streak  # newly achieved streak value


# ─────────────────────────────────────────────
# GUEST / DEMO MODE INFRASTRUCTURE
# ─────────────────────────────────────────────

def _is_guest(request):
    """Return True if the visitor is in guest/demo mode (no account)."""
    return request.session.get('guest_mode', False)


def _calc_stats_xp(stats):
    return int(getattr(stats, 'xp_total', 0) or 0)


def _user_xp(user, stats=None):
    if stats is not None and getattr(stats, 'xp_total', None) is not None:
        return int(stats.xp_total or 0)
    from core.xp import get_user_xp
    return get_user_xp(user)


def _exam_attempt_id(request, subject):
    if not getattr(request.user, 'is_authenticated', False):
        return None
    from core.xp import create_activity
    act = create_activity(request.user, 'exam', subject or 'general', {})
    return str(act.token)


def _cached_quiz_scores(user, serie_subjects, diag_scores, spa_mode=False):
    """Scores par matière — cache court en navigation SPA (DB distante)."""
    from django.core.cache import cache
    from core.subject_scores import get_scores_for_user

    if not spa_mode:
        return get_scores_for_user(user, serie_subjects, diag_scores)
    cache_key = f'quiz_scores:{user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    scores = get_scores_for_user(user, serie_subjects, diag_scores)
    cache.set(cache_key, scores, 60)
    return scores


def _cached_league_context(user, my_xp, spa_mode=False):
    """Classement global + ligue — cache court en navigation SPA."""
    from django.core.cache import cache
    from django.db.models import F
    from core.models import UserStats

    my_league_tier = my_xp // 1000
    cache_key = f'league_ctx:{user.pk}:{my_league_tier}'
    if spa_mode:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    _all_stats = UserStats.objects.filter(
        user__is_staff=False,
        user__is_superuser=False,
        user__agent__isnull=True,
    )
    global_rank = _all_stats.filter(xp_total__gt=my_xp).count() + 1
    league_slice = list(
        _all_stats.filter(
            xp_total__gte=my_league_tier * 1000,
            xp_total__lt=(my_league_tier + 1) * 1000,
        ).select_related('user', 'user__profile').order_by('-xp_total')[:30]
    )
    league_rank = 1
    for i, s in enumerate(league_slice):
        if s.user_id == user.id:
            league_rank = i + 1
            break
    league_data = []
    from accounts.names import alias_map_for, overlay_alias
    aliases = alias_map_for(user)
    for i, s in enumerate(league_slice):
        prof = getattr(s.user, 'profile', None)
        display = (prof.first_name if prof and prof.first_name else s.user.username)
        league_data.append({
            'rank': i + 1,
            'name': overlay_alias(aliases, s.user_id, display),
            'xp': int(s.xp_total or 0),
            'is_me': s.user_id == user.id,
        })
    result = {
        'global_rank': global_rank,
        'league_rank': league_rank,
        'league_data': league_data,
        'my_league_tier': my_league_tier,
    }
    if spa_mode:
        cache.set(cache_key, result, 90)
    return result


def _public_league_snapshot(limit=10):
    """Real leaderboard slice for landing / guest demo (no fake names)."""
    from django.core.cache import cache
    from django.db.models import F
    from core.models import UserStats

    cache_key = f'public_league_snapshot:{limit}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    base_stats = UserStats.objects.filter(
        user__is_staff=False,
        user__is_superuser=False,
        user__agent__isnull=True,
    )
    total = base_stats.count()
    top = list(base_stats.select_related('user', 'user__profile').order_by('-xp_total')[:limit])
    league_data = []
    for i, s in enumerate(top):
        prof = getattr(s.user, 'profile', None)
        name = (prof.first_name if prof and prof.first_name else s.user.username)
        league_data.append({
            'rank': i + 1,
            'name': name,
            'xp': int(s.xp_total or 0),
            'is_me': False,
        })
    result = {
        'total_students': total,
        'league_data': league_data,
        'top_user_name': league_data[0]['name'] if league_data else '—',
        'top_user_xp': league_data[0]['xp'] if league_data else 0,
    }
    cache.set(cache_key, result, 60)
    return result


def _guest_platform_coaching_cards():
    """Cartes démo réalistes (scores / conseils) pour le mode visiteur."""
    g = _GUEST_DEMO
    cards = []
    for i, c in enumerate(g.get('coaching_cards') or []):
        cards.append({
            'id': f'guest-demo-{i}',
            'type': 'action',
            'icon': c.get('icon', 'fas fa-lightbulb'),
            'color': c.get('color', '#a78bfa'),
            'priority': i + 1,
            'title': c.get('title', ''),
            'description': c.get('description', ''),
            'action_label': c.get('action_label', 'Voir'),
            'action_url': c.get('action_url', '/dashboard/'),
            'badge': c.get('badge', 'Démo'),
            'badge_color': c.get('color', '#a78bfa'),
        })
    if not cards:
        cards = [
            {
                'id': 'guest-signup',
                'type': 'action',
                'icon': 'fas fa-user-plus',
                'color': '#38bdf8',
                'priority': 1,
                'title': 'Crée ton compte gratuit',
                'description': 'Sauvegarde ta progression et débloque le coach personnalisé.',
                'action_label': "S'inscrire",
                'action_url': '/signup/',
                'badge': '2 min',
                'badge_color': '#38bdf8',
            },
        ]
    return cards


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
    return list(_get_cours_chapters_cached(subject_norm))


@functools.lru_cache(maxsize=32)
def _get_cours_chapters_cached(subject_norm: str) -> tuple:
    chapters = pdf_loader.get_chapters_from_note_json(subject_norm)
    return tuple(chapters) if chapters else tuple()


def _cours_progress_by_chapter(user, user_subjs):
    """Progression chapitres sans charger le JSON messages (très lourd sur DB distante)."""
    from django.core.cache import cache

    cache_key = f'cours_prog:{user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    step_pct = {0: 5, 1: 15, 2: 30, 3: 50}
    progress = {}
    for sess in CourseSession.objects.filter(
        user=user,
        chapter_subject__in=list(user_subjs),
        chapter_num__isnull=False,
    ).only('chapter_subject', 'chapter_num', 'status', 'progress_step'):
        subj = (sess.chapter_subject or '').strip().lower()
        num = sess.chapter_num
        if not subj or num is None:
            continue
        if sess.status == 'completed':
            pct = 100
        else:
            step = int(sess.progress_step or 0)
            pct = step_pct.get(step, min(95, step * 10))
        key = (subj, int(num))
        progress[key] = max(progress.get(key, 0), pct)
    cache.set(cache_key, progress, 45)
    return progress

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
    'my_xp': 2840,
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
    'weaknesses': [('svt', 55), ('maths', 58), ('histoire', 63)],
    'league_data': [
        {'rank': 1, 'name': 'Marie T.', 'xp': 6200, 'is_me': False},
        {'rank': 2, 'name': 'Jean-Paul', 'xp': 5400, 'is_me': False},
        {'rank': 3, 'name': 'Claudia M.', 'xp': 4900, 'is_me': False},
        {'rank': 4, 'name': 'Hervé R.', 'xp': 4300, 'is_me': False},
        {'rank': 5, 'name': 'Sophia V.', 'xp': 3700, 'is_me': False},
        {'rank': 6, 'name': 'André L.', 'xp': 3200, 'is_me': False},
        {'rank': 7, 'name': 'Visiteur (vous)', 'xp': 2840, 'is_me': True},
        {'rank': 8, 'name': 'Patrick D.', 'xp': 2100, 'is_me': False},
        {'rank': 9, 'name': 'Fabiola N.', 'xp': 1650, 'is_me': False},
        {'rank': 10, 'name': 'Ricot B.', 'xp': 1200, 'is_me': False},
    ],
    'recent_sessions': [
        {'subject': 'maths',    'score': 6,  'total': 10, 'display': 'Mathématiques — 6/10'},
        {'subject': 'chimie',   'score': 8,  'total': 10, 'display': 'Chimie — 8/10'},
        {'subject': 'physique', 'score': 7,  'total': 10, 'display': 'Physique — 7/10'},
    ],
    # Coaching cards (shown on dashboard + progression)
    'coaching_cards': [
        {
            'title': 'SVT — Priorité urgente',
            'description': 'Ton score en SVT (55%) est le plus bas de ta série. La génétique mendélienne et la division cellulaire sont tes points faibles : méiose, lois de Mendel et arbres généalogiques reviennent souvent au BAC.',
            'icon': 'fas fa-dna',
            'color': '#f87171',
            'badge': '⚠ Urgent',
            'action_url': '/dashboard/cours/?subject=svt',
            'action_label': 'Cours SVT',
        },
        {
            'title': 'Mathématiques — Dérivées & Intégrales',
            'description': 'Tes 58% en Maths montrent des lacunes en calcul différentiel. Pratique les dérivées de fonctions composées et les intégrales par parties — ces sujets représentent ~30% du BAC Maths.',
            'icon': 'fas fa-calculator',
            'color': '#fb923c',
            'badge': '⬆ À améliorer',
            'action_url': '/dashboard/quiz/?subject=maths',
            'action_label': 'Quiz Maths',
        },
        {
            'title': 'Histoire — Chronologie à consolider',
            'description': 'À 63% en Histoire, tu perds des points sur les repères chronologiques (1804–1915). Une fiche timeline + 2 quiz ciblés cette semaine te feront gagner rapidement.',
            'icon': 'fas fa-landmark',
            'color': '#a78bfa',
            'badge': '📈 À consolider',
            'action_url': '/dashboard/quiz/?subject=histoire',
            'action_label': 'Quiz Histoire',
        },
        {
            'title': 'Kreyòl — Continue comme ça !',
            'description': 'Excellent travail (80%) ! Tu maîtrises bien la compréhension et la production écrite. Pour viser l\'excellence, entraîne-toi sur l\'analyse de texte et le commentaire littéraire.',
            'icon': 'fas fa-pen-nib',
            'color': '#34d399',
            'badge': '✓ Fort',
            'action_url': '/dashboard/exercices/?subject=francais',
            'action_label': 'Exercices Kreyòl',
        },
    ],
    'coach_advice': (
        '<strong>🎯 Analyse de ta progression</strong><br><br>'
        'Après analyse de tes résultats démo, voici mes recommandations prioritaires :<br><br>'
        '⚠️ <strong>SVT (55%)</strong> — Priorité n°1. Revois la méiose et les lois de Mendel : '
        'croisements dihybrides et arbres généalogiques reviennent très souvent au BAC.<br><br>'
        '📊 <strong>Maths (58%)</strong> — Consolide dérivées et intégrales (fonctions composées, intégration par parties).<br><br>'
        '✅ <strong>Kreyòl (80%)</strong> et <strong>Chimie (72%)</strong> — Très bon niveau ! '
        'Maintiens ces acquis tout en renforçant SVT et Maths.<br><br>'
        '<em>💡 Conseil du coach : un plan de 8 semaines à ~2h/jour te permettrait d\'approcher 1 400/1 900 au BAC estimé.</em>'
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
        'summary': '🎯 Plan personnalisé démo : priorité SVT + Maths, consolidation Histoire, maintien Kreyòl et Chimie. ~2h/jour recommandées.',
        'weeks': [
            {
                'label': 'Sem. 1',
                'focus': 'SVT — Génétique mendélienne',
                'days': [
                    {'day': 'Lundi', 'subject': 'SVT', 'task': 'Lois de Mendel + exercices de croisement monohybride', 'duration_min': 90, 'priority': 'high'},
                    {'day': 'Mardi', 'subject': 'Mathématiques', 'task': 'Dérivées : fonctions composées — cours + 10 exercices', 'duration_min': 90, 'priority': 'high'},
                    {'day': 'Mercredi', 'subject': 'Histoire', 'task': 'Timeline 1804–1915 + fiche mémo', 'duration_min': 60, 'priority': 'medium'},
                    {'day': 'Vendredi', 'subject': 'SVT', 'task': 'Méiose vs mitose — diagrammes + quiz 10 questions', 'duration_min': 60, 'priority': 'high'},
                    {'day': 'Samedi', 'subject': 'Chimie', 'task': 'Oxydoréduction : révision + TD 5-8', 'duration_min': 60, 'priority': 'medium'},
                ],
            },
            {
                'label': 'Sem. 2',
                'focus': 'Maths — Intégrales & Physique',
                'days': [
                    {'day': 'Lundi', 'subject': 'Mathématiques', 'task': 'Intégrales : primitives usuelles + calcul d\'aires', 'duration_min': 90, 'priority': 'high'},
                    {'day': 'Mardi', 'subject': 'Physique', 'task': 'Cinématique : trajectoires et vitesses', 'duration_min': 75, 'priority': 'medium'},
                    {'day': 'Mercredi', 'subject': 'Philosophie', 'task': 'Liberté & déterminisme — plan de dissertation', 'duration_min': 60, 'priority': 'medium'},
                    {'day': 'Jeudi', 'subject': 'SVT', 'task': 'ADN et synthèse des protéines — quiz BAC', 'duration_min': 60, 'priority': 'high'},
                    {'day': 'Samedi', 'subject': 'Kreyòl', 'task': 'Commentaire de texte — méthode + entraînement', 'duration_min': 90, 'priority': 'low'},
                ],
            },
            {
                'label': 'Sem. 3',
                'focus': 'Consolidation transversale',
                'days': [
                    {'day': 'Lundi', 'subject': 'Mathématiques', 'task': 'Suites arithmétiques & géométriques — exercices BAC', 'duration_min': 90, 'priority': 'medium'},
                    {'day': 'Mardi', 'subject': 'Physique', 'task': 'Lois de Newton — problèmes de dynamique', 'duration_min': 90, 'priority': 'medium'},
                    {'day': 'Jeudi', 'subject': 'Histoire', 'task': 'Révision chronologie + quiz 15 questions', 'duration_min': 75, 'priority': 'high'},
                    {'day': 'Vendredi', 'subject': 'Anglais', 'task': 'Reading comprehension + vocabulaire BAC', 'duration_min': 60, 'priority': 'medium'},
                    {'day': 'Samedi', 'subject': 'Chimie', 'task': 'Thermochimie : enthalpie, loi de Hess', 'duration_min': 60, 'priority': 'medium'},
                ],
            },
        ],
    },
    # Progression chapitres cours (démo réaliste, 0–100)
    'course_progress': {
        'svt': 35, 'maths': 42, 'chimie': 68, 'physique': 55,
        'philosophie': 60, 'histoire': 48, 'anglais': 58, 'francais': 75,
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
            from core.daily_missions import build_guest_daily_missions
            g = _GUEST_DEMO
            platform = _public_league_snapshot()
            _g_stats = _SN(
                exercices_resolus=7,
                quiz_completes=12,
                minutes_etude=135,
                xp_total=g['my_xp'],
            )
            guest_serie_pending = request.session.pop('guest_serie_pending', False)
            request.session.modified = True
            _serie_choices = [
                ('SVT', '🧬', 'Sciences de la Vie et de la Terre',   'SVT · Chimie · Physique · Maths'),
                ('SMP', '⚗️', 'Sciences Mathématiques et Physiques', 'Maths · Physique · Chimie · SVT'),
                ('SES', '📊', 'Sciences Économiques et Sociales',    'Économie · Histoire · Philo · Maths'),
                ('LLA', '📚', 'Lettres, Langues et Arts',            'Philo · Kreyòl · Anglais · Art'),
            ]
            ctx = {
                'username': g['username'],
                'username': g['username'],
                'first_name': g['first_name'],
                'streak': g['streak'],
                'heures_etude': g['heures_etude'],
                'minutes_rest': g['minutes_rest'],
                'avg_score': g['avg_score'],
                'bac_score': g['bac_score'],
                'bac_gap_pass': g['bac_gap_pass'],
                'bac_gap_target': g['bac_gap_target'],
                'bac_next_milestone_pts': g['bac_next_milestone_pts'],
                'bac_next_milestone_label': g['bac_next_milestone_label'],
                'bac_prev_milestone_pts': g['bac_prev_milestone_pts'],
                'bac_milestone_pct': g['bac_milestone_pct'],
                'next_exo_milestone': g['next_exo_milestone'],
                'exo_milestone_pct': g['exo_milestone_pct'],
                'exo_focus_subject': g['exo_focus_subject'],
                'user_serie_subjects': g['user_serie_subjects'],
                'league_data': platform['league_data'],
                'my_xp': g['my_xp'],
                'current_level': g['current_level'],
                'league_rank': None,
                'league_name': g['league_name'],
                'league_color': g['league_color'],
                'xp_progress_pct': g['xp_progress_pct'],
                'global_rank': None,
                'total_students': platform['total_students'],
                'mats': MATS,
                'profile': None,
                'stats': _g_stats,
                'is_guest': True,
                'guest_serie_pending': guest_serie_pending,
                'serie_choices': _serie_choices,
                'has_diagnostic': True,
                'diagnostic_in_progress': False,
                'daily_missions': build_guest_daily_missions(),
                'quiz_scores': g['quiz_scores'],
                'strengths': g['strengths'],
                'weaknesses': g['weaknesses'],
                'coaching_cards': g['coaching_cards'],
                'recent_sessions': g['recent_sessions'],
            }
            from core.spotlights import get_home_spotlights
            ctx.update(get_home_spotlights())
            return render(request, 'core/dashboard.html', ctx)
        return redirect('/login/?next=' + request.get_full_path())
    spa_mode = getattr(request, 'spa_mode', False)
    _streak_just_earned = _update_streak(request.user)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    stats       = _get_or_create_stats(request.user)

    # Score par matière — source unique (quiz + exo + cours)
    diag_scores = {d.subject: d.score for d in DiagnosticResult.objects.filter(user=request.user)}
    diag_result_count = len(diag_scores)
    has_diagnostic = diag_result_count > 0
    diagnostic_in_progress = bool(request.session.get('diagnostic_qs'))
    _user_subjs = set(_get_user_serie_subjects(request.user))
    quiz_scores = _cached_quiz_scores(request.user, _user_subjs, diag_scores, spa_mode=spa_mode)

    # Check if school is missing
    school_missing = not profile.school
    
    sorted_asc  = sorted(quiz_scores.items(), key=lambda x: x[1])
    sorted_desc = sorted(quiz_scores.items(), key=lambda x: x[1], reverse=True)
    # Seuils : force >= 70%, lacune < 65%
    strengths  = [(s, v) for s, v in sorted_desc if v >= 70][:3]
    weaknesses = [(s, v) for s, v in sorted_asc  if v <  65][:3]

    avg_score = round(sum(quiz_scores.values()) / len(quiz_scores)) if quiz_scores else 0

    # Note BAC — source unique (coefficients officiels)
    _user_serie_key = profile.serie or 'SVT'
    bac_score = estimate_bac_score(quiz_scores, _user_serie_key, SERIES)

    heures_etude = stats.minutes_etude // 60
    minutes_rest = stats.minutes_etude % 60

    # ── XP / League ──────────────────────────────────────────
    my_xp = _user_xp(request.user, stats)

    league_ctx = _cached_league_context(request.user, my_xp, spa_mode=spa_mode)
    global_rank = league_ctx['global_rank']
    league_rank = league_ctx['league_rank']
    league_data = league_ctx['league_data']
    my_league_tier = league_ctx['my_league_tier']

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
        'school_missing':           school_missing,
        'has_diagnostic':           has_diagnostic,
        'diagnostic_in_progress':   diagnostic_in_progress,
    }

    # Données de maîtrise adaptative pour le dashboard
    if spa_mode:
        context['masteries'] = {}
    else:
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

    from django.core.cache import cache
    from core.daily_missions import build_daily_missions
    _weak_label = MATS.get(exo_focus_subject, {}).get('label') if exo_focus_subject else None
    _serie_label = SERIES.get(_user_serie_key, {}).get('label', _user_serie_key)
    dm_cache_key = f'daily_missions:{request.user.pk}'
    daily_missions = cache.get(dm_cache_key) if spa_mode else None
    if daily_missions is None:
        daily_missions = build_daily_missions(
            request.user,
            profile,
            stats,
            weaknesses=weaknesses,
            exo_focus_subject=exo_focus_subject,
            weak_subject_label=_weak_label,
            serie_label=_serie_label,
        )
        if spa_mode:
            cache.set(dm_cache_key, daily_missions, 60)
    context['daily_missions'] = daily_missions
    from core.spotlights import get_home_spotlights
    context.update(get_home_spotlights())

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
                PublicDemoQA.objects.values('matiere', 'question', 'answer', 'updated_at')[:12]
            )
            if not demo_qa:
                demo_qa = [
                    {
                        'matiere': 'maths',
                        'question': 'Comment dériver f(x) = x² · sin(x) ?',
                        'answer': 'Produit : f\'(x) = 2x·sin(x) + x²·cos(x).',
                        'updated_at': None,
                    },
                    {
                        'matiere': 'svt',
                        'question': 'Quelle est la différence entre mitose et méiose ?',
                        'answer': 'Mitose = cellules filles identiques ; méiose = gamètes haploïdes avec brassage.',
                        'updated_at': None,
                    },
                    {
                        'matiere': 'chimie',
                        'question': 'Qu\'est-ce qu\'un acide selon Brønsted ?',
                        'answer': 'Un donneur de proton H⁺.',
                        'updated_at': None,
                    },
                ]
            return render(request, 'core/chat.html', {
                'mats': {k: v for k, v in MATS.items() if k in _GUEST_DEMO['user_serie_subjects']},
                'conversations': [],
                'preload_subject': '',
                'preload_message': '',
                'preload_session': '',
                'last_chat_subject': 'svt',
                'is_guest': True,
                'demo_qa': demo_qa,
            })
        return redirect('/login/?next=' + request.get_full_path())
    # ── Historique (panneau chat) : toutes les conversations ──
    conv_list = _list_chat_conversations(request.user)

    # Filtrer les matières selon la série du user
    user_subjs = _get_user_serie_subjects(request.user)
    chat_mats = {k: v for k, v in MATS.items() if k in user_subjs}

    last_chat_subject = (
        ChatMessage.objects.filter(user=request.user)
        .exclude(subject='')
        .exclude(subject='general')
        .order_by('-created_at')
        .values_list('subject', flat=True)
        .first()
    )
    if last_chat_subject not in chat_mats:
        last_chat_subject = 'svt' if 'svt' in chat_mats else next(iter(chat_mats), '')

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
        'last_chat_subject': last_chat_subject,
        'is_premium': user_is_premium,
        'chat_remaining': chat_remaining,
        'pdf_path': request.GET.get('pdf', ''),
        'pdf_name': request.GET.get('pdf_name', ''),
        'pdf_text': request.GET.get('pdf_text', ''),
        'chat_greeting': _chat_greeting_for(request.user),
    })


@require_POST
def chat_api(request):
    import uuid
    import traceback

    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Session expiree. Reconnecte-toi pour continuer.'}, status=401)

    # ── Premium gate: 2 messages/jour gratuits ──
    from core.premium import can_use_chat, increment_chat, premium_required_json, is_daily_ai_limit_reached, daily_limit_reached_json
    allowed, remaining = can_use_chat(request.user)
    if not allowed:
        if is_daily_ai_limit_reached(request.user):
            return JsonResponse(daily_limit_reached_json(), status=429)
        return JsonResponse(premium_required_json(), status=403)
    
    try:
        raw_text = request.POST.get('message', '').strip()
        subject = (request.POST.get('subject', '') or '').strip().lower()
        session_key = request.POST.get('session_key', '') or uuid.uuid4().hex[:16]
        image = request.FILES.get('image')

        from core.chat_token_optimizer import (
            split_pdf_from_message,
            build_pdf_context,
            needs_user_profile,
            wants_exercise_explanation,
            needs_ai_synthesis,
            format_local_chat_reply,
            trim_chat_context,
            CHAT_RAG_MAX_BLOCKS,
            CHAT_RAG_MAX_CHARS,
            CHAT_CONTEXT_MAX_CHARS,
            CHAT_HISTORY_LOAD,
        )

        user_query, pdf_name, pdf_excerpt = split_pdf_from_message(raw_text)
        text = user_query or raw_text

        if not text:
            return JsonResponse({'error': 'Message vide.'}, status=400)

        if not subject or subject == 'general':
            return JsonResponse({'error': 'Choisis d abord une matiere.'}, status=400)

        if subject not in MATS:
            return JsonResponse({'error': 'Matiere invalide.'}, status=400)

        user_lang = _get_user_lang(request)

        # Bypass IA : salutations, fillers, FAQ en cache
        local_reply = local_responses.try_local_chat_response(
            text,
            subject=subject,
            user_lang=user_lang,
            subject_label=_get_subj_label(subject),
            has_image=bool(request.FILES.get('image')),
        )
        if local_reply:
            try:
                ChatMessage.objects.create(
                    user=request.user, role='user', content=text,
                    subject=subject, session_key=session_key,
                )
                ChatMessage.objects.create(
                    user=request.user, role='ai', content=local_reply,
                    subject=subject, session_key=session_key,
                )
                increment_chat(request.user)
                try:
                    from core.xp import settle_daily_missions
                    settle_daily_missions(request.user)
                except Exception:
                    pass
            except Exception as save_err:
                print(f"[SAVE_ERROR] {save_err}")
            return JsonResponse({
                'reply': local_reply,
                'followups': [],
                'session_key': session_key,
                'local': True,
                'title': _persist_and_return_chat_title(request.user, session_key, text, subject),
            })

        image_data = None
        image_mime = None
        try:
            image_data = image.read() if image else None
            image_mime = image.content_type if image else None
        except Exception:
            image_data = None
            image_mime = None
        if image_data:
            image_data, image_mime = gemini.prepare_image_bytes(image_data, image_mime)

        # 1) Recherche locale: on construit un contexte fiable depuis les JSON.
        result = ''
        rag_top_score = 0.0
        skip_rag = local_responses.is_conversation_filler(text) and not image_data
        if not skip_rag:
            try:
                rag_out = _search_ai_blocks(
                    subject, chapter_num=0, query=text, max_blocks=CHAT_RAG_MAX_BLOCKS,
                    max_output_chars=CHAT_RAG_MAX_CHARS,
                    summary_only=True,
                    return_top_score=True,
                )
                result, rag_top_score = rag_out
            except Exception as block_err:
                print(f"[AI_BLOCK_SEARCH_ERROR] {subject}: {block_err}")
                result = ''
                rag_top_score = 0.0

            if not result:
                try:
                    result = _get_db_context(subject, user_message=text)
                except Exception as ctx_err:
                    print(f"[DB_CONTEXT_ERROR] {subject}: {ctx_err}")
                    result = ''

        local_context = ''
        local_fallback_reply = ''
        exercise_reformat_only = False

        # If the user asks for an exercise/example, build a compact targeted local context.
        best_exo = _pick_best_local_exercise_block(subject, text)
        if best_exo:
            from core.chat_exercise_local import (
                format_exercise_question_for_chat,
                build_exercise_reformat_context,
                split_exercise_qa,
            )
            chapter = best_exo.get('chapter', '').strip() or 'Chapitre non precise'
            subchapter = best_exo.get('subchapter', '').strip() or 'Sous-chapitre non precise'
            content = (best_exo.get('content', '') or '').strip()
            question_part, _ = split_exercise_qa(content)
            q_only = re.sub(r'^question\s*:\s*', '', question_part, flags=re.I).strip()
            if len(q_only) > 1600:
                q_only = q_only[:1600].rstrip() + '\n\n...'

            local_exo_reply = format_exercise_question_for_chat(best_exo, _get_subj_label(subject))
            if local_exo_reply and not wants_exercise_explanation(text):
                local_fallback_reply = local_exo_reply
                local_context = ''  # pas besoin de contexte lourd
            else:
                exercise_reformat_only = True
                local_context = build_exercise_reformat_context(best_exo)
                local_fallback_reply = local_exo_reply or format_local_chat_reply(
                    _get_subj_label(subject),
                    q_only or content[:1200],
                    chapter=chapter,
                    subchapter=subchapter,
                )
        elif result:
            snippet = result.strip()
            if len(snippet) > 3200:
                snippet = snippet[:3200].rstrip() + '\n\n...'
            local_context = snippet
            local_fallback_reply = format_local_chat_reply(
                _get_subj_label(subject),
                snippet,
            )
        else:
            local_fallback_reply = (
                f"Je n ai pas trouve de passage pertinent dans les notes JSON de {_get_subj_label(subject)}. "
                "Essaie avec des mots-cles plus precis."
            )

        pdf_context = build_pdf_context(pdf_name, pdf_excerpt)
        history_len = 0
        try:
            history_len = ChatMessage.objects.filter(
                user=request.user,
                subject=subject,
                session_key=session_key,
            ).count()
        except Exception:
            history_len = 0

        skip_ai = False
        if not image_data:
            if (
                best_exo
                and local_fallback_reply
                and not exercise_reformat_only
                and not wants_exercise_explanation(text)
            ):
                skip_ai = True
            elif local_context and not best_exo and not needs_ai_synthesis(
                text,
                history_len=history_len,
                has_image=False,
                has_pdf=bool(pdf_excerpt),
                rag_top_score=rag_top_score,
            ):
                skip_ai = True

        # 2) Appel IA seulement si la synthèse est nécessaire.
        reply = ''
        used_local_path = False
        if skip_ai:
            reply = local_fallback_reply
            used_local_path = True
        elif local_context or pdf_context or exercise_reformat_only:
            history = []
            try:
                history_qs = ChatMessage.objects.filter(
                    user=request.user,
                    subject=subject,
                    session_key=session_key,
                ).order_by('-created_at')[:CHAT_HISTORY_LOAD]
                for msg in reversed(list(history_qs)):
                    role = 'model' if msg.role == 'ai' else 'user'
                    history.append({'role': role, 'parts': [msg.content]})
            except Exception:
                history = []

            user_profile = None
            if needs_user_profile(text, history_len) and not exercise_reformat_only:
                try:
                    user_profile = gemini.build_user_learning_profile_short(request.user, subject=subject)
                except Exception:
                    user_profile = None

            context_parts = []
            if pdf_context:
                context_parts.append(pdf_context)
            if local_context:
                context_parts.append(local_context)
            ai_db_context = trim_chat_context('\n\n'.join(context_parts), max_chars=CHAT_CONTEXT_MAX_CHARS)

            ai_message = text
            if exercise_reformat_only:
                ai_message = (
                    'Reformule cet exercice pour un élève BAC : énoncé clair, '
                    'une seule problème, sans donner la solution.'
                )

            try:
                ai_reply = gemini.get_chat_response(
                    message=ai_message,
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
                    try:
                        local_responses.cache_faq_answer(subject, text, reply)
                    except Exception:
                        pass
            except Exception as ai_err:
                print(f"[CHAT_GEMINI_ERROR] {subject}: {ai_err}")

        if not reply:
            if pdf_excerpt or '📄 **PDF:' in raw_text or 'Analyse' in text:
                reply = (
                    "Désolé, je rencontre une petite difficulté technique pour analyser ce document à l'instant. "
                    "Peux-tu me reposer une question plus précise sur son contenu ?"
                )
            else:
                reply = local_fallback_reply
                used_local_path = True

        print(
            f"[CHAT_HYBRID] subject={subject} query={text[:60]} "
            f"reply_len={len(reply)} local={used_local_path} rag_score={rag_top_score:.0f}"
        )

        # SAUVEGARDER l'échange (message utilisateur = question courte, pas le PDF entier)
        try:
            if text:
                stored_user_msg = text
                if pdf_name and pdf_excerpt:
                    stored_user_msg = f"{text}\n\n📄 {pdf_name}"
                ChatMessage.objects.create(
                    user=request.user, role='user', content=stored_user_msg,
                    subject=subject, session_key=session_key,
                )
            ChatMessage.objects.create(user=request.user, role='ai', content=reply, subject=subject, session_key=session_key)
            increment_chat(request.user)
            try:
                from core.xp import settle_daily_missions
                settle_daily_missions(request.user)
            except Exception:
                pass
            if text and reply and not used_local_path:
                from .learning_tracker import schedule_chat_learning_updates
                schedule_chat_learning_updates(request.user, session_key, text, reply, subject)
        except Exception as save_err:
            print(f"[SAVE_ERROR] {str(save_err)}\n{traceback.format_exc()}")
            # Continue même si la sauvegarde échoue

        return JsonResponse({
            'reply': reply,
            'followups': [],
            'session_key': session_key,
            'local': used_local_path,
            'title': _persist_and_return_chat_title(request.user, session_key, text, subject),
        })

    except Exception as e:
        _logger.exception('Server error')
        error_msg = 'Erreur interne du serveur.'
        print(f"[CHAT_ERROR] {error_msg}\n{traceback.format_exc()}")
        return JsonResponse({'error': error_msg}, status=500)


@login_required
def api_ai_usage_snapshot(request):
    """Micro-log tokens : utilisateur voit sa conso du jour ; staff voit la marge globale."""
    from core.ai_usage import get_user_usage_today, get_margin_snapshot_today

    if request.user.is_staff:
        return JsonResponse({'ok': True, 'scope': 'global', **get_margin_snapshot_today()})
    return JsonResponse({'ok': True, 'scope': 'user', **get_user_usage_today(request.user)})


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
            'created_at': _local_time(m.created_at).strftime('%H:%M'),
        })
    subject = msgs.first().subject
    return JsonResponse({'messages': data, 'subject': subject, 'session_key': session_key})


@login_required
def api_chat_conversations(request):
    """Liste / recherche l'historique : q cherche dans le texte des messages."""
    q = (request.GET.get('q') or '').strip()
    convs = _list_chat_conversations(request.user, q=q)
    data = []
    for c in convs:
        last = c.get('last_msg')
        when = ''
        if last:
            try:
                when = _local_time(last).strftime('%d/%m %H:%M')
            except Exception:
                when = ''
        data.append({
            'session_key': c['session_key'],
            'subject': c.get('subject') or '',
            'label': c.get('label') or '',
            'preview': c.get('preview') or '',
            'msg_count': c.get('msg_count') or 0,
            'when': when,
        })
    return JsonResponse({'ok': True, 'q': q, 'conversations': data})


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
            # Pas de matière détectée → pas de dump multi-matières (économie tokens).
            _SKIP_GEN = {'bonjou', 'bonswa', 'salut', 'alo', 'ok', 'merci', 'mesi', 'dako', 'super', 'hi', 'hello', 'bye', 'au revoir'}
            _msg_tmp = user_message.strip().lower()
            if len(_msg_tmp) < 10 or _msg_tmp in _SKIP_GEN:
                return ''
            return ''
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
            note_context = _search_ai_blocks(
                subject, chapter_num=0, query=user_message, max_blocks=6,
                max_output_chars=AI_BLOCK_MAX_OUTPUT_CHARS,
                summary_only=True,
            )
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
    from core.extra_bet_grader import normalize_text
    return normalize_text(text)


def _verify_extra_bet_submission_with_ai(subject: str, question_type: str, prompt: str, answer: str, options: list) -> dict:
    """Validation à la publication — règles locales, 0 IA."""
    from core.extra_bet_grader import verify_extra_bet_submission
    return verify_extra_bet_submission(question_type, prompt, answer, options)


def extra_bet_view(request):
    from core.extra_bet_leaderboard import get_week_top_creators
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
        top_creators, week_label = get_week_top_creators(3)
        return render(request, 'core/extra_bet.html', {
            'mats': MATS,
            'posts': posts,
            'active_subject': active_subject,
            'active_sort': sort,
            'subject_labels': subject_labels,
            'subject_colors': subject_colors,
            'top_creators': top_creators,
            'week_label': week_label,
            'current_user_id': None,
            'is_guest': True,
        })

    # ── Authenticated: show page ──
    from core.premium import is_premium
    is_premium_user = is_premium(request.user)

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
    from accounts.names import alias_map_for, overlay_alias, public_name
    aliases = alias_map_for(request.user)
    for p in posts:
        try:
            fallback = p.user.profile.get_display_name()
        except Exception:
            fallback = public_name(p.user)
        p.author_display = overlay_alias(aliases, p.user_id, fallback)
    # Build subject label map for template
    subject_labels = json.dumps({k: v['label'] for k, v in MATS.items()})
    subject_colors = json.dumps({k: v['color'] for k, v in MATS.items()})
    top_creators, week_label = get_week_top_creators(3)
    for c in top_creators:
        fallback = c.get('user__profile__first_name') or c.get('user__username') or ''
        c['user__profile__first_name'] = overlay_alias(aliases, c.get('user__id'), fallback)
    return render(request, 'core/extra_bet.html', {
        'mats': filtered_mats,
        'posts': posts,
        'active_subject': active_subject,
        'active_sort': sort,
        'subject_labels': subject_labels,
        'subject_colors': subject_colors,
        'top_creators': top_creators,
        'week_label': week_label,
        'current_user_id': request.user.id,
        'is_premium': is_premium_user,
    })


@login_required
@require_POST
def api_extra_bet_create(request):
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Payload invalide.'}, status=400)

    subject = str(data.get('subject', '')).strip().lower()
    question_type = str(data.get('question_type', 'word')).strip().lower()
    prompt = str(data.get('prompt', '')).strip()
    answer_raw = data.get('answer', '')
    if isinstance(answer_raw, dict):
        answer = json.dumps(answer_raw, ensure_ascii=False)
    else:
        answer = str(answer_raw or '').strip()

    options_raw = data.get('options')
    if question_type == 'qcm':
        options = options_raw if isinstance(options_raw, list) else []
        options = [str(o).strip() for o in options if str(o).strip()]
    elif question_type in ('match', 'parts'):
        options = options_raw if isinstance(options_raw, dict) else {}
    else:
        options = options_raw if isinstance(options_raw, list) else []

    allowed_types = {'word', 'qcm', 'match', 'parts', 'direct', 'fill'}
    if subject not in MATS:
        return JsonResponse({'ok': False, 'error': 'Matiere invalide.'}, status=400)
    if question_type not in allowed_types:
        return JsonResponse({'ok': False, 'error': 'Type de question invalide.'}, status=400)
    if not answer:
        return JsonResponse({'ok': False, 'error': 'Reponse manquante.'}, status=400)

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
    answer_raw = data.get('answer', '')
    if isinstance(answer_raw, dict):
        submitted = json.dumps(answer_raw, ensure_ascii=False)
    else:
        submitted = str(answer_raw or '').strip()
    if not post_id or not submitted:
        return JsonResponse({'ok': False, 'error': 'Reponse manquante.'}, status=400)

    try:
        post = ExtraBetPost.objects.get(id=post_id)
    except ExtraBetPost.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Publication introuvable.'}, status=404)

    expected = str(post.answer or '').strip()
    from core.extra_bet_grader import grade_extra_bet_answer
    is_correct, display_answer = grade_extra_bet_answer(
        submitted, expected, post.question_type, post.options or [],
    )

    # Guests: return result without recording attempt in DB
    if not _is_guest(request) and request.user.is_authenticated:
        from core.premium import is_premium, can_use_extra_bet, increment_extra_bet
        if not is_premium(request.user):
            can_use, _ = can_use_extra_bet(request.user)
            if not can_use:
                return JsonResponse({
                    'ok': False,
                    'error': 'limit_reached',
                    'message': 'Limite gratuite atteinte (3 questions/jour). Reviens demain ou passe Premium !'
                }, status=403)
            increment_extra_bet(request.user)
            
        ExtraBetAttempt.objects.update_or_create(
            post=post,
            user=request.user,
            defaults={
                'submitted_answer': submitted,
                'is_correct': is_correct,
            },
        )
        from core.push_events import push_extra_bet_answer
        push_extra_bet_answer(request.user, post, is_correct)
    responders_count = ExtraBetAttempt.objects.filter(post=post).values('user').distinct().count()
    creators_count = ExtraBetPost.objects.filter(subject=post.subject).values('user').distinct().count()

    return JsonResponse({
        'ok': True,
        'is_correct': is_correct,
        'correct_answer': display_answer,
        'responders_count': responders_count,
        'creators_count': creators_count,
    })


@require_POST
def api_extra_bet_ai_help(request):
    """Indice local — 0 IA (pas de synonymes inventés, juste structure)."""
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Payload invalide.'}, status=400)

    post_id = data.get('post_id')
    if not post_id:
        return JsonResponse({'ok': False, 'error': 'Publication introuvable.'}, status=400)

    try:
        post = ExtraBetPost.objects.get(id=post_id)
    except ExtraBetPost.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Publication introuvable.'}, status=404)

    primary = (str(post.answer or '').split('|')[0].strip())
    qtype = (post.question_type or 'word').lower()
    if qtype in ('direct', 'fill'):
        qtype = 'word'

    if qtype == 'qcm':
        hint = 'QCM : élimine les options incohérentes avant de choisir.'
    elif qtype == 'match':
        hint = 'Relie chaque élément de gauche à la bonne définition / valeur à droite.'
    elif qtype == 'parts':
        hint = 'Chaque sous-question a une valeur exacte (nombre ou mot court). Pas d\'espace dans les mots.'
    else:
        hint = 'Réponds en un seul mot, sans phrase complète.'

    return JsonResponse({'ok': True, 'reply': hint})


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
        from core.push_events import push_extra_bet_like
        push_extra_bet_like(request.user, post)

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


def _auto_seed_quiz_questions(subject: str, target: int = 40, max_attempts: int = 1) -> int:
    """
    Génère des QCM via l'IA depuis les JSON d'examens structurés.
    Priorité : get_exam_context_json (rapide, varié) → fallback PDF brut.
    Retourne le nombre total de questions disponibles après seeding.
    """
    try:
        for attempt in range(max_attempts):
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
    """Lance le seeding dans un thread séparé (désactivé par défaut en prod)."""
    from core.ai_usage import ENABLE_QUIZ_BACKGROUND_SEED, try_acquire_quiz_seed_lock
    if not ENABLE_QUIZ_BACKGROUND_SEED:
        return
    if not try_acquire_quiz_seed_lock(subject):
        return
    import threading
    def _run():
        try:
            _auto_seed_quiz_questions(subject, target=target, max_attempts=2)
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

    _quiz_pers = {}
    if user is not None:
        try:
            from .learning_tracker import get_quiz_personalization
            _quiz_pers = get_quiz_personalization(user, subject)
        except Exception:
            pass

    def _quiz_ai_kw(**extra):
        kw = {
            'weak_topics': _quiz_pers.get('weak_topics') or None,
            'serie_key': _quiz_pers.get('serie_key', ''),
            'user_profile': _quiz_pers.get('user_profile', ''),
        }
        kw.update(extra)
        return kw

    if subject == 'anglais':
        from pathlib import Path as _angPath
        import json as _angj, random as _angr
        _ang_file = _angPath(__file__).resolve().parent.parent / 'database' / 'quiz_anglais.json'
        try:
            _ang_raw = _angj.loads(_ang_file.read_text(encoding='utf-8'))
            _ang_qs = _ang_raw if isinstance(_ang_raw, list) else _ang_raw.get('quiz', [])
            if not _ang_qs:
                raise ValueError('empty')
            _ang_qs = list(_ang_qs)
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
        direct_qs = gemini.generate_quiz_questions(subject, count=new_count, chapter=chapter, **_quiz_ai_kw())
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
            _esp_qs = list(_esp_qs)
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
        direct_qs = gemini.generate_quiz_questions(subject, count=new_count, chapter=chapter, **_quiz_ai_kw())
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
            _kr_qs = list(_kr_qs)
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
            _j_qs = list(_j_qs)
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
        direct_qs = gemini.generate_quiz_questions(subject, count=new_count, exam_context=exam_ctx, **_quiz_ai_kw())
        if direct_qs:
            random.shuffle(direct_qs)
            return {'questions': review_qs + direct_qs, 'source': 'ai_direct', 'review_count': len(review_qs)}
        return {'error': 'Questions indisponibles. Réessaie dans quelques secondes.', 'questions': []}

    if total_available < new_count:
        total_available = _auto_seed_quiz_questions(subject, target=40, max_attempts=1)
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
    from core.premium import can_use_quiz, increment_quiz, premium_required_json, is_daily_ai_limit_reached, daily_limit_reached_json
    allowed, remaining = can_use_quiz(request.user)
    if not allowed:
        if is_daily_ai_limit_reached(request.user):
            return JsonResponse(daily_limit_reached_json(), status=429)
        return JsonResponse(premium_required_json(), status=403)

    try:
        subject = request.GET.get('subject', 'maths')
        chapter = request.GET.get('chapter', '')
        count = int(request.GET.get('count', 10))
        payload = get_quiz_questions_for_user(request.user, subject=subject, count=count, chapter=chapter, include_review=True)
        qs = payload.get('questions') or []
        if qs:
            from core.xp import create_activity
            act = create_activity(request.user, 'quiz', subject, {
                'questions': [
                    {
                        'enonce': (q.get('enonce') or '')[:800],
                        'options': list(q.get('options') or [])[:8],
                        'reponse_correcte': q.get('reponse_correcte', 0),
                    }
                    for q in qs
                ],
            })
            payload['attempt_id'] = str(act.token)
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
            return JsonResponse({'ok': True, 'guest': True, 'signup_url': '/signup/'})
        return JsonResponse({'error': 'login_required'}, status=401)
    from datetime import date
    from django.utils import timezone as _tz
    from .models import MistakeTracker, XpActivity
    from core import xp_config as _xp_c
    from core.xp import consume_activity, grant_xp, settle_daily_missions

    subject = data.get('subject', 'maths')
    details = data.get('details', []) or []
    attempt_id = (data.get('attempt_id') or '').strip()
    xp_gained = 0
    xp_reason = 'no_attempt'
    session_valid = False
    activity_kind = None
    activity_token_str = ''
    server_score = sum(1 for d in details if d.get('ok'))
    server_total = max(len(details), int(data.get('total') or 0) or 1)

    if attempt_id:
        try:
            from uuid import UUID
            token = UUID(str(attempt_id))
        except (ValueError, TypeError):
            token = None
        kind = None
        if token:
            kind = XpActivity.objects.filter(token=token, user=request.user).values_list('kind', flat=True).first()
        if token and kind in ('quiz', 'exam'):
            activity_kind = kind
            activity_token_str = str(token)
            min_s = _xp_c.QUIZ_MIN_SECONDS if kind == 'quiz' else _xp_c.EXAM_MIN_SECONDS
            with transaction.atomic():
                act, status = consume_activity(request.user, token, kind, min_s)
                if status == 'invalid_activity':
                    xp_reason = 'invalid_activity'
                elif status == 'too_fast':
                    xp_reason = 'too_fast'
                elif status == 'already_consumed':
                    xp_reason = 'already_consumed'
                elif act is not None:
                    stored = (act.payload or {}).get('questions') or []
                    if kind == 'quiz' and stored:
                        by_enonce = {(s.get('enonce') or '')[:800]: s for s in stored}
                        graded_ok = 0
                        answered = 0
                        for d in details:
                            en = (d.get('question') or '')[:800]
                            sq = by_enonce.get(en)
                            if not sq:
                                continue
                            answered += 1
                            opts = sq.get('options') or []
                            try:
                                rc = int(sq.get('reponse_correcte', 0))
                            except (TypeError, ValueError):
                                rc = 0
                            correct_txt = opts[rc] if 0 <= rc < len(opts) else ''
                            chosen = d.get('chosen') or d.get('user_answer')
                            d['ok'] = bool(correct_txt) and chosen == correct_txt
                            if d['ok']:
                                graded_ok += 1
                        ratio = answered / max(len(stored), 1)
                        server_score, server_total = graded_ok, max(len(stored), 1)
                        if ratio >= _xp_c.QUIZ_MIN_ANSWER_RATIO:
                            session_valid = True
                            xp_reason = 'ok'
                            if status != 'already_consumed':
                                act.consumed_at = _tz.now()
                                act.save(update_fields=['consumed_at'])
                        else:
                            xp_reason = 'incomplete'
                    else:
                        # Examen blanc : session valide, pas d'XP hors mission.
                        session_valid = True
                        xp_reason = 'ok'
                        if status != 'already_consumed':
                            act.consumed_at = _tz.now()
                            act.save(update_fields=['consumed_at'])

    score = server_score
    total = server_total
    session = QuizSession.objects.create(
        user=request.user, subject=subject,
        score=score, total=total, details=details
    )

    if session_valid:
        stats = _get_or_create_stats(request.user)
        stats.quiz_completes += 1
        stats.minutes_etude += 10
        stats.save(update_fields=['quiz_completes', 'minutes_etude'])
        try:
            quiz_ratio = _xp_c.score_ratio(score, total)
            if activity_kind == 'exam':
                amount = _xp_c.xp_from_score(_xp_c.XP_EXAM_MAX, ratio=quiz_ratio)
                src, cap, ref_prefix = _xp_c.SOURCE_EXAM, _xp_c.DAILY_CAP_EXAM, 'exam'
            else:
                amount = _xp_c.xp_from_score(_xp_c.XP_QUIZ_MAX, ratio=quiz_ratio)
                if quiz_ratio >= 0.8:
                    amount += _xp_c.XP_QUIZ_PERFECT_BONUS
                src, cap, ref_prefix = _xp_c.SOURCE_QUIZ, _xp_c.DAILY_CAP_QUIZ, 'quiz'
            res = grant_xp(
                request.user, amount, src,
                f'{ref_prefix}:{activity_token_str or session.pk}',
                extra={'score': score, 'total': total, 'ratio': quiz_ratio},
                daily_cap=cap,
            )
            xp_gained = res.amount if res.granted else 0
            mission_results = settle_daily_missions(request.user, scores={'quiz': quiz_ratio})
            xp_gained += sum(r.amount for r in mission_results if r.granted)
            xp_reason = 'ok' if xp_gained else (res.reason if not res.granted else 'ok')
        except Exception:
            pass

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
        from .learning_tracker import invalidate_ai_caches
        invalidate_ai_caches(request.user)
    except Exception as _lt_err:
        print(f"[LEARNING_TRACKER] quiz: {_lt_err}")

    return JsonResponse({
        'ok': True, 'score': score, 'total': total,
        'pct': session.get_percentage(), 'session_id': session.pk,
        'xp_gained': xp_gained, 'xp_reason': xp_reason,
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
    return _build_exercices_chapters_cached()


@functools.lru_cache(maxsize=1)
def _build_exercices_chapters_cached():
    """Cached — lit les JSON disque une seule fois par process."""
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
            from .exo_loader import normalize_chapter_key
            for _ch in _phys_chaps:
                _ch['display'] = _PHYS_DISPLAY_MAP.get(_ch['title'], _ch['title'])
                _ch['chapter_key'] = normalize_chapter_key(_ch['title']) or _ch['display']
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
        if subj in ('anglais', 'espagnol'):
            try:
                from . import lang_exo_loader as _lang_exo
                from .exo_loader import normalize_chapter_key
                _lang_chaps = _lang_exo.get_chapters(subj)
                if _lang_chaps:
                    for _ch in _lang_chaps:
                        _ch['chapter_key'] = normalize_chapter_key(_ch['title']) or _ch['title']
                    chapters_by_subject[subj] = _lang_chaps
                    continue
            except Exception:
                pass
        if subj in ('maths', 'chimie'):
            try:
                from . import exo_loader as _exo_loader_subj
                from .exo_loader import normalize_chapter_key
                _subj_chaps = _exo_loader_subj.get_chapters(subj)
                if _subj_chaps:
                    for _ch in _subj_chaps:
                        _ch['chapter_key'] = normalize_chapter_key(_ch['title']) or _ch['title']
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
            try:
                from . import exo_loader as _exo_loader_svt
                _svt_chaps = _exo_loader_svt.get_chapters('svt')
                if _svt_chaps:
                    from .exo_loader import normalize_chapter_key
                    for _ch in _svt_chaps:
                        _ch['chapter_key'] = normalize_chapter_key(_ch['title']) or _ch['title']
                        _ch['display'] = _ch['title']
                    chapters_by_subject['svt'] = _svt_chaps
                    continue
            except Exception:
                pass
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
                    def _entry_title(c):
                        if not isinstance(c, dict):
                            return ''
                        return (
                            c.get('titre') or c.get('title') or c.get('chapter_title')
                            or c.get('name') or ''
                        ).strip()
                    _parsed = [
                        {'id': i+1, 'title': _entry_title(c), 'num': i+1}
                        for i, c in enumerate(_chs)
                        if _entry_title(c)
                    ]
                    if _parsed:
                        chapters_by_subject[subj] = _parsed
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
    profile = UserProfile.objects.filter(user=request.user).only(
        'serie', 'langue_etrangere', 'coach_name', 'first_name',
    ).first()
    if profile is None:
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
        if image_data:
            image_data, image_mime = gemini.prepare_image_bytes(image_data, image_mime)

        result = gemini.solve_exercise(text, image_data, image_mime)

        fp_src = (text or '')[:1500]
        if image:
            fp_src += f'|img:{image.size}:{image_mime}'
        fp = hashlib.sha256(fp_src.encode('utf-8', errors='ignore')).hexdigest()[:32]
        token = None
        try:
            body_token = request.POST.get('activity_token') or ''
            token = body_token or None
        except Exception:
            token = None
        from core.xp import reward_exercise
        if token:
            xp_res = reward_exercise(request.user, fp, token=token, orphan=False)
        else:
            xp_res = reward_exercise(request.user, fp, orphan=True)
        # Solve photo/texte : pas d'XP activité (orphelin ou hors mission).
        if xp_res.reason not in ('no_token', 'invalid_activity', 'too_fast', 'no_fingerprint', 'no_activity_xp'):
            stats = _get_or_create_stats(request.user)
            stats.exercices_resolus += 1
            stats.minutes_etude += 5
            stats.save(update_fields=['exercices_resolus', 'minutes_etude'])

        return JsonResponse({
            'solution': result,
            'xp_gained': xp_res.amount if xp_res.granted else 0,
            'xp_reason': xp_res.reason,
        })
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
            # Quota invité consommé au 1er message chat (pas au chargement)
        else:
            return JsonResponse({'error': 'login_required'}, status=401)

    # ── Premium gate: 1 exercice par jour gratuit (skip for guests) ──
    if request.user.is_authenticated:
        from core.premium import can_use_exercise, increment_exercise, premium_required_json, is_daily_ai_limit_reached, daily_limit_reached_json
        allowed, remaining = can_use_exercise(request.user)
        if not allowed:
            if is_daily_ai_limit_reached(request.user):
                return JsonResponse(daily_limit_reached_json(), status=429)
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
        from core.exo_loader import normalize_chapter_key

        def _lookup_chapter_rule(chapter_name: str, rules: dict):
            if not chapter_name:
                return None
            norm = normalize_chapter_key(chapter_name)
            if norm in rules:
                return rules[norm]
            low = chapter_name.lower().strip()
            if low in rules:
                return rules[low]
            for key, rule in rules.items():
                kn = normalize_chapter_key(key) or key.lower()
                if norm and (norm in kn or kn in norm):
                    return rule
            return None

        _chapter_rule = _lookup_chapter_rule(chapter, _CHAPTER_RULES) if chapter else None

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
            if exercise_data:
                from .exercise_tutor import init_session, session_public_view, opening_message
                _student = request.user.first_name or request.user.username or 'Élève' if request.user.is_authenticated else 'Élève'
                _coach = ''
                if request.user.is_authenticated:
                    try:
                        from accounts.models import UserProfile
                        _coach = (UserProfile.objects.get(user=request.user).coach_name or '').strip()
                    except Exception:
                        pass
                _session = init_session(exercise_data)
                return JsonResponse({
                    'ok': True,
                    'exercise': exercise_data,
                    'session': session_public_view(_session, exercise_data),
                    'opening': opening_message(exercise_data, _student, coach_name=_coach),
                    'session_state': _session,
                })
            return JsonResponse({'error': 'Aucun exercice disponible.'}, status=404)

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

        # ── 0. Langues : vrais exercices (notes + examens), IA seulement en dernier ──
        LANGUAGE_SUBJECTS = {'anglais', 'espagnol', 'kreyol'}
        if subject in LANGUAGE_SUBJECTS:
            try:
                from . import lang_exo_loader as _lang_exo
                _lex = _lang_exo.get_random_exercise(subject, chapter)
                if _lex:
                    exercise_data = {
                        'intro': _lex.get('intro') or '',
                        'enonce': _lex.get('enonce') or _lex.get('intro') or '',
                        'texte': _lex.get('texte') or '',
                        'questions': _lex.get('questions') or [],
                        'reponses': _lex.get('reponses') or {},
                        'theme': (_lex.get('theme') or _lex.get('chapter') or subject.upper()).strip(),
                        'matiere': subject.upper(),
                        'difficulte': _lex.get('difficulte') or 'moyen',
                        'source': _lex.get('source') or '',
                        'solution': '',
                        'conseils': '',
                        '_is_real_bac': True,
                    }
                    try:
                        from .exercise_display import format_exercise_display_local
                        _fmt = format_exercise_display_local(
                            subject, exercise_data['intro'], exercise_data['questions'],
                        )
                        exercise_data['intro'] = _fmt['intro']
                        exercise_data['enonce'] = _fmt['intro']
                        exercise_data['questions'] = _fmt['questions']
                    except Exception as _fmt_err:
                        print(f'[api_get_exercise] lang format error: {_fmt_err}')
            except Exception as _lang_err:
                print(f'[api_get_exercise] lang_exo_loader error: {_lang_err}')
            if not exercise_data:
                try:
                    exercise_data = gemini.generate_language_exercise(subject, chapter)
                    if exercise_data and not exercise_data.get('questions'):
                        exercise_data = None
                except Exception:
                    exercise_data = None

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
                            'reponses':   _exo.get('reponses', {}),
                            'theme':      (_exo.get('theme') or _exo.get('chapter') or subject.upper()).strip(),
                            'matiere':    subject.upper(),
                            'difficulte': 'moyen',
                            'source':     _exo.get('source', ''),
                            'solution':   '',
                            'conseils':   '',
                            '_is_real_bac': True,
                        }
                        # ── Formatage local (tables, LaTeX) — zéro appel IA ──
                        try:
                            from .exercise_display import format_exercise_display_local
                            _fmt = format_exercise_display_local(
                                subject,
                                exercise_data['intro'],
                                exercise_data['questions'],
                            )
                            exercise_data['intro']     = _fmt['intro']
                            exercise_data['enonce']    = _fmt['intro']
                            exercise_data['questions'] = _fmt['questions']
                        except Exception as _fmt_err:
                            print(f'[api_get_exercise] format_exercise_display_local error: {_fmt_err}')
            except Exception as _exo_err:
                print(f'[api_get_exercise] exo_loader error: {_exo_err}')

        # ── 1a. Programme note_*_ai.json — énoncé seul, zéro IA ─────────────
        _NOTE_AI_SUBJECTS = {'maths', 'chimie', 'physique', 'economie'}
        if not exercise_data and subject in _NOTE_AI_SUBJECTS:
            try:
                from core.chat_exercise_local import (
                    pick_note_ai_exercise,
                    block_to_exercise_payload,
                    apply_exercise_display_polish,
                )
                _nai_block = pick_note_ai_exercise(subject, chapter)
                if _nai_block:
                    _nai_candidate = block_to_exercise_payload(_nai_block, subject)
                    if _nai_candidate and _respects_chapter_rule(_nai_candidate):
                        exercise_data = apply_exercise_display_polish(_nai_candidate, subject)
            except Exception as _nai_err:
                print(f'[api_get_exercise] note_ai error: {_nai_err}')

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

        # ── 3. Fallback : JSON structuré parsé (0 appel IA) ──────────────────
        if not exercise_data:
            candidate = pdf_loader.get_exercise_from_json(subject, chapter)
            if candidate and _respects_chapter_rule(candidate):
                candidate['_is_real_bac'] = False
                exercise_data = candidate

        # ── 4. Fallback: IA live sur texte brut exam ─────────────────────────
        AI_SUBJECTS = {'maths', 'physique', 'chimie', 'svt'}
        if not exercise_data and subject in AI_SUBJECTS:
            raw_texts = pdf_loader.get_raw_exam_texts_for_ai(subject, chapter, max_chars=4000)
            if raw_texts:
                candidate = gemini.extract_structured_exercise(raw_texts, subject, chapter)
                if candidate and _respects_chapter_rule(candidate):
                    candidate['_is_real_bac'] = False
                    exercise_data = candidate

        # ── 5. Fallback final : IA pure (pas pour les langues : énoncés incomplets) ──
        if not exercise_data and subject not in LANGUAGE_SUBJECTS:
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

        # 2. Si questions manquantes, extraire a)/1. depuis l'énoncé (sans IA)
        questions = [str(q).strip() for q in (exercise_data.get('questions') or []) if str(q).strip()]
        if len(questions) < 2:
            try:
                from .exo_loader import _extract_sub_questions
                intro = exercise_data.get('intro') or exercise_data.get('enonce', '')
                intro_clean, extracted = _extract_sub_questions(intro)
                if extracted:
                    if intro_clean:
                        exercise_data['intro'] = intro_clean
                        exercise_data['enonce'] = intro_clean
                    exercise_data['questions'] = extracted
            except Exception:
                pass
        try:
            from .exercise_display import format_exercise_display_local
            _fmt = format_exercise_display_local(
                subject,
                exercise_data.get('intro') or exercise_data.get('enonce') or '',
                exercise_data.get('questions') or [],
            )
            exercise_data['intro'] = _fmt['intro']
            exercise_data['enonce'] = _fmt['intro']
            if _fmt['questions']:
                exercise_data['questions'] = _fmt['questions']
        except Exception:
            pass

        from .exercise_tutor import init_session, session_public_view, opening_message
        from core.chat_exercise_local import exercise_public_payload

        _student = 'Élève'
        _coach = ''
        if request.user.is_authenticated:
            _student = request.user.first_name or request.user.username or _student
            try:
                _prof = UserProfile.objects.get(user=request.user)
                _coach = (_prof.coach_name or '').strip()
            except Exception:
                pass
        _session = init_session(exercise_data)
        _fp_raw = (
            str(exercise_data.get('id') or '')
            + '|' + (exercise_data.get('titre') or '')[:200]
            + '|' + (exercise_data.get('enonce') or exercise_data.get('intro') or '')[:800]
        )
        _fp = hashlib.sha256(_fp_raw.encode('utf-8', errors='ignore')).hexdigest()[:32]
        _exo_token = None
        if request.user.is_authenticated:
            from core.xp import create_activity
            from .models import XpActivity
            _act = create_activity(
                request.user, XpActivity.KIND_EXERCISE, subject,
                {'fingerprint': _fp},
            )
            _exo_token = str(_act.token)

        _public_exercise = exercise_data
        if exercise_data.get('_from_note_ai') and exercise_data.get('solution'):
            try:
                from django.core.cache import cache as _dj_cache
                _uid = request.user.id if request.user.is_authenticated else 'guest'
                _dj_cache.set(
                    f'exo_sol:{_uid}:{exercise_data.get("_note_ai_id", "")}',
                    exercise_data['solution'],
                    timeout=3600,
                )
            except Exception:
                pass
            _public_exercise = exercise_public_payload(exercise_data)

        return JsonResponse({
            'ok': True,
            'exercise': _public_exercise,
            'session': session_public_view(_session, exercise_data),
            'opening': opening_message(exercise_data, _student, coach_name=_coach),
            'session_state': _session,
            'activity_token': _exo_token,
        })
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

        xp_gained = 0
        xp_reason = 'partial'
        # Track stats
        if question_index is None:
            from core.xp import reward_exercise
            _fp_raw = (
                str(exercise.get('id') or '')
                + '|' + (exercise.get('titre') or '')[:200]
                + '|' + (exercise.get('enonce') or exercise.get('intro') or '')[:800]
            )
            _fp = hashlib.sha256(_fp_raw.encode('utf-8', errors='ignore')).hexdigest()[:32]
            token = body.get('activity_token')
            _gs = result.get('global_score', 0)
            _ms = result.get('max_score', 1) or 1
            _pct = round(float(_gs) / float(_ms) * 100) if _ms else 0
            xp_res = reward_exercise(
                request.user, _fp, token=token, orphan=False, score_pct=_pct,
            )
            xp_reason = xp_res.reason
            if xp_res.granted:
                xp_gained = xp_res.amount
            # Compteur exo même sans XP (mission déjà prise / plafond)
            if xp_res.reason not in ('no_token', 'invalid_activity', 'too_fast', 'no_fingerprint'):
                stats = _get_or_create_stats(request.user)
                stats.exercices_resolus += 1
                stats.minutes_etude += 8
                stats.save(update_fields=['exercices_resolus', 'minutes_etude'])

            # ── Suivi adaptatif — exercice corrigé ─────────────────────────
            try:
                from .learning_tracker import update_subject_mastery, log_learning_event
                update_subject_mastery(
                    user=request.user,
                    subject=subject,
                    is_correct=_pct >= 60,
                    question_text=(exercise.get('titre') or exercise.get('enonce') or '')[:200],
                    score_pct=float(_pct),
                )
                wrong_samples = []
                for corr in (result.get('corrections') or [])[:6]:
                    if corr.get('correct') or corr.get('partial'):
                        continue
                    wrong_samples.append({
                        'question': str(corr.get('question') or '')[:160],
                        'student_answer': str(corr.get('student_answer') or '')[:120],
                        'expected_key': str(corr.get('expected_key') or '')[:120],
                    })
                log_learning_event(
                    user=request.user,
                    event_type='exercise_corrected',
                    subject=subject,
                    details={
                        'score': _gs, 'max': _ms, 'pct': _pct,
                        'exercise_title': (exercise.get('titre') or exercise.get('enonce') or '')[:100],
                        'wrong_samples': wrong_samples[:4],
                        'wrong_count': len(wrong_samples),
                    },
                    score_pct=float(_pct),
                )
            except Exception as _lt_err:
                print(f"[LEARNING_TRACKER] exercise: {_lt_err}")

        return JsonResponse({'ok': True, 'xp_gained': xp_gained, 'xp_reason': xp_reason, **result})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


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

        from .exam_parser import merge_student_answers
        safe_pairs = merge_student_answers(safe_pairs, mise_au_net)

        # Guest exam usage is counted at launch in api_generate_exam_v2.

        user_lang = _get_user_lang(request)
        part_a_earned = float(body.get('part_a_earned', 0) or 0)
        part_a_max = float(body.get('part_a_max', 0) or 0)
        if not getattr(settings, 'DEEPSEEK_API_KEY', ''):
            total_pts = sum(float(q.get('pts', 0) or 0) for q in safe_pairs)
            return JsonResponse({
                'ok': False,
                'error': 'correction_unavailable',
                'corrections': [],
                'estimated_score': 0,
                'total_pts': total_pts,
                'global_feedback': (
                    'La correction automatique est temporairement indisponible. '
                    'Compare tes réponses avec le corrigé ou réessaie plus tard.'
                ),
            }, status=503)
        try:
            result = gemini.correct_exam_open_answers(subject, safe_pairs, user_lang=user_lang, mise_au_net=mise_au_net)
            part_b_eff = max(0.0, 100.0 - part_a_max) if part_a_max else float(result.get('total_pts', 0) or 0)
            part_b_scaled = 0.0
            total_pts_b = float(result.get('total_pts', 0) or 0)
            est_b = float(result.get('estimated_score', 0) or 0)
            if total_pts_b > 0 and part_a_max:
                part_b_scaled = round(est_b / total_pts_b * part_b_eff, 1)
            grand_score = round(part_a_earned + part_b_scaled, 1) if part_a_max else est_b
            skills = result.get('skills_breakdown') or {}
            skills['partie_a'] = {'earned': part_a_earned, 'max': part_a_max}
            skills['grand_total'] = {'earned': grand_score, 'max': 100.0}
            return JsonResponse({
                'ok': True,
                **result,
                'part_a_earned': part_a_earned,
                'part_a_max': part_a_max,
                'part_b_scaled': part_b_scaled,
                'part_b_eff_max': part_b_eff,
                'grand_score': grand_score,
                'skills_breakdown': skills,
            })
        except Exception:
            _logger.exception('AI exam correction unavailable')
            total_pts = sum(float(q.get('pts', 0) or 0) for q in safe_pairs)
            return JsonResponse({
                'ok': False,
                'error': "La correction IA est temporairement indisponible. Verifie la configuration de la cle IA puis reessaie.",
                'corrections': [
                    {
                        'question': q.get('question', ''),
                        'student_answer': q.get('student_answer', ''),
                        'scored_pts': 0,
                        'max_pts': float(q.get('pts', 0) or 0),
                        'status': 'empty',
                        'feedback': 'Correction IA non disponible pour le moment.'
                    }
                    for q in safe_pairs
                ],
                'estimated_score': 0,
                'total_pts': total_pts,
                'global_feedback': 'La correction automatique n a pas pu demarrer. Tes reponses sont conservees: reessaie apres correction de la configuration IA.',
            }, status=503)
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
    try:
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
                'student_name': '',
            })
        if not request.user.is_authenticated:
            return redirect('/login/?next=' + request.get_full_path())
        user_subjs = _get_user_serie_subjects(request.user)
        exam_mats = {k: v for k, v in MATS.items() if k in user_subjs}
        if not exam_mats:
            exam_mats = dict(MATS)
        return render(request, 'core/examen_blanc.html', {
            'mats': exam_mats,
            'student_name': request.user.get_full_name() or request.user.first_name or request.user.username,
        })
    except Exception as _e:
        _logger.exception('examen_blanc_view error')
        return render(request, 'core/examen_blanc.html', {
            'mats': dict(MATS),
            'error_msg': "Une erreur temporaire s'est produite. Réessaie dans quelques instants.",
            'student_name': '',
        })


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

        # Une seule tentative — les retries multipliaient les coûts API
        exam_data = {}
        for _attempt in range(1):
            exam_data = gemini.generate_structured_exam(exam_text, subject)

        if not exam_data:
            import traceback; traceback.print_exc()
            return JsonResponse({'error': 'Génération échouée. Le modèle n\'a pas retourné de JSON valide. Réessaie dans quelques secondes.'}, status=500)
        return JsonResponse({'exam': exam_data})
    except Exception as e:
        import traceback; traceback.print_exc()
        _logger.exception('Server error')
        return JsonResponse({'error': 'Erreur interne du serveur.'}, status=500)


def _get_exam_exclude_hashes(request, subject: str) -> set:
    """Hashes d'items déjà vus : DB (connecté) ou paramètre seen_hashes (invité)."""
    if request.user.is_authenticated:
        return set(
            UserSeenExamItem.objects
            .filter(user=request.user, subject=subject)
            .values_list('item_hash', flat=True)[:500]
        )

    raw = (request.GET.get('seen_hashes') or '').strip()
    if not raw:
        return set()
    return {h.strip() for h in raw.split(',') if len(h.strip()) == 64}


def _record_exam_item_hashes(request, subject: str, exam_data: dict) -> list:
    """Enregistre les hashes des items servis ; retourne la liste pour le client invité."""
    from .exam_item_registry import extract_exam_item_hashes

    hashes = extract_exam_item_hashes(exam_data)
    if not hashes:
        return hashes

    if request.user.is_authenticated:
        UserSeenExamItem.objects.bulk_create(
            [
                UserSeenExamItem(user=request.user, subject=subject, item_hash=h)
                for h in hashes
            ],
            ignore_conflicts=True,
        )
    return hashes


def api_generate_exam_v2(request):
    """
    Génère un examen blanc BAC Haïti de HAUTE QUALITÉ depuis la base de données.
    Pas de PDFs — l'IA génère du contenu ORIGINAL (dissertations, textes, exercices).
    Garde exactement le format de rendu de la page Examen Blanc.

    Logique cache Railway DB :
      1. Cherche un examen déjà généré (même matière/série) non encore vu par cet user.
      2. Si trouvé → servir directement (0 appel API).
      3. Si épuisé → générer via DeepSeek, stocker en DB, marquer comme vu.
    """
    import time as _time, traceback as _tb
    subject = request.GET.get('subject', 'maths')
    try:
        if _is_guest(request):
            guest_exam_done = request.session.get('guest_exam_done', 0)
            if guest_exam_done >= 1:
                return JsonResponse({'error': 'guest_limit', 'signup_url': '/signup/'}, status=403)

        _user_serie = ''
        try:
            _user_serie = request.user.profile.serie or ''
        except Exception:
            pass

        _exclude_hashes = _get_exam_exclude_hashes(request, subject)

        # ── ÉTAPE 1 : Chercher un examen en cache non encore vu ──────────────
        _is_authenticated = request.user.is_authenticated
        if _is_authenticated:
            cached_exam = (
                GeneratedExam.objects
                .filter(subject=subject, serie=_user_serie)
                .exclude(seen_by=request.user)
                .order_by('?')
                .first()
            )
            if cached_exam:
                cached_exam.seen_by.add(request.user)
                item_hashes = _record_exam_item_hashes(request, subject, cached_exam.exam_data)
                _logger.info(f'[exam_cache] Served cached exam #{cached_exam.pk} for {subject}/{_user_serie} to {request.user.username}')
                return JsonResponse({
                    'exam': cached_exam.exam_data,
                    'cached': True,
                    'item_hashes': item_hashes,
                    'attempt_id': _exam_attempt_id(request, subject),
                })

        # ── ÉTAPE 2 : Générer un nouvel examen ───────────────────────────────
        # Pull quality quiz questions as thematic reference
        db_questions = list(QuizQuestion.objects.filter(subject=subject).order_by('?')[:16])

        exam_data = {}
        last_err = ''
        for _attempt in range(1):
            try:
                exam_data = gemini.generate_exam_from_db(
                    subject,
                    quiz_questions=db_questions,
                    user_serie=_user_serie,
                    exclude_hashes=_exclude_hashes,
                )
                if exam_data and exam_data.get('parts'):
                    break
            except Exception as _e:
                last_err = str(_e)
                _tb.print_exc()

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
                            strict_exam = gemini.generate_exam_from_db(
                                subject,
                                quiz_questions=db_questions,
                                user_serie=_user_serie,
                                exclude_hashes=_exclude_hashes,
                            )
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
                'ok': False,
                'error': "L'IA est momentanément indisponible."
            })

        if _is_guest(request):
            guest_exam_done = int(request.session.get('guest_exam_done', 0) or 0)
            request.session['guest_exam_done'] = guest_exam_done + 1
            request.session.modified = True

        # ── ÉTAPE 3 : Variation IA optionnelle (désactivée par défaut — coût) ─
        from core.ai_usage import ENABLE_EXAM_AI_ENHANCE
        if ENABLE_EXAM_AI_ENHANCE:
            try:
                exam_data = gemini.ai_enhance_exam(exam_data, subject)
            except Exception:
                _tb.print_exc()  # Non-bloquant

        # ── ÉTAPE 4 : Sauvegarder en cache Railway DB ─────────────────────────
        if _is_authenticated and exam_data and exam_data.get('parts'):
            try:
                saved_exam = GeneratedExam.objects.create(
                    subject=subject,
                    serie=_user_serie,
                    exam_data=exam_data,
                )
                saved_exam.seen_by.add(request.user)
                _logger.info(f'[exam_cache] Served new exam #{saved_exam.pk} for {subject}/{_user_serie}')
            except Exception:
                _tb.print_exc()  # Non-bloquant — l'examen est quand même servi

        item_hashes = _record_exam_item_hashes(request, subject, exam_data)
        return JsonResponse({
            'exam': exam_data,
            'item_hashes': item_hashes,
            'attempt_id': _exam_attempt_id(request, subject),
        })

    except Exception as e:
        _logger.exception('api_generate_exam_v2 error')
        return JsonResponse({'ok': False, 'error': "L'IA est momentanément indisponible."})


# ─────────────────────────────────────────────
# PROGRESSION — scores unifiés (core/subject_scores.py)
# ─────────────────────────────────────────────
from core.subject_scores import (
    compute_all_blended_scores as _compute_all_blended_scores,
    compute_subject_blended_score as _compute_subject_blended_score,
    get_scores_for_user,
    estimate_bac_score,
)


def progression_view(request):
    if not request.user.is_authenticated:
        if _is_guest(request):
            from types import SimpleNamespace
            g = _GUEST_DEMO
            mats_extended = {}
            for k, v in MATS.items():
                if k not in g['user_serie_subjects']:
                    continue
                mats_extended[k] = dict(v)
                score = g['quiz_scores'].get(k, 55)
                mats_extended[k]['quiz_score'] = score
                mats_extended[k]['sessions'] = 2 + (score % 4)
                mats_extended[k]['exo_count'] = 1 + (score % 3)
                mats_extended[k]['quiz_avg'] = score
                mats_extended[k]['exo_pct'] = min(95, score + 8)
                mats_extended[k]['has_course'] = True
                mats_extended[k]['exo_total'] = mats_extended[k]['exo_count']
            mock_profile = SimpleNamespace(streak=g['streak'], school='Lycée Demo', serie='SVT', avatar=None)
            mock_stats = SimpleNamespace(exercices_resolus=7, quiz_completes=12, minutes_etude=135, xp_total=g['my_xp'])
            study_insights = []
            for subj, sc in g['weaknesses']:
                info = MATS.get(subj, {})
                study_insights.append({
                    'subject': subj,
                    'label': info.get('label', subj),
                    'score': sc,
                    'priority': 'haute' if sc < 55 else 'moyenne',
                    'chapter_num': 1,
                    'chapter': 'Chapitre prioritaire',
                    'subtopics': ['Révisions ciblées', 'Exercices BAC'],
                    'weakness_reason': f'Score démo {sc}% — à renforcer avant le BAC.',
                    'cours_url': f'/dashboard/cours/?subject={subj}',
                    'quiz_url': f'/dashboard/quiz/?subject={subj}',
                    'exo_url': f'/dashboard/exercices/?subject={subj}',
                    'quiz_category': '',
                })
            quiz_sessions = [
                SimpleNamespace(
                    subject=s['subject'],
                    score=s['score'],
                    total=s['total'],
                    completed_at=_timezone.now(),
                    get_percentage=lambda s=s: round(100 * s['score'] / max(1, s['total'])),
                )
                for s in g['recent_sessions']
            ]
            return render(request, 'core/progression.html', {
                'is_guest': True,
                'mats': mats_extended,
                'diag_scores': g['quiz_scores'],
                'heures_etude': g['heures_etude'],
                'minutes_rest': g['minutes_rest'],
                'avg_score': g['avg_score'],
                'bac_score': g['bac_score'],
                'bac_gap_pass': g['bac_gap_pass'],
                'bac_gap_target': g['bac_gap_target'],
                'stats': mock_stats,
                'profile': mock_profile,
                'quiz_sessions': quiz_sessions,
                'user_serie_subjects': g['user_serie_subjects'],
                'study_insights': study_insights,
                'coach_advice': g['coach_advice'],
                'coaching_cards': g['coaching_cards'],
                'mastery_display': [
                    {
                        'label': MATS.get(subj, {}).get('label', subj),
                        'color': MATS.get(subj, {}).get('color', '#6366f1'),
                        'score': sc,
                        'level': 'en progrès' if sc < 70 else 'solide',
                        'correct': max(1, sc // 10),
                        'total': 10,
                        'weak_topics': ['Révision'],
                    }
                    for subj, sc in list(g['quiz_scores'].items())[:6]
                ],
                'study_recs': [
                    {
                        'subject': subj,
                        'label': MATS.get(subj, {}).get('label', subj),
                        'mastery': sc,
                        'reason': 'Priorité démo — renforce cette matière.',
                    }
                    for subj, sc in g['weaknesses']
                ],
                'chat_summaries': [],
            })
        return redirect('/login/?next=' + request.get_full_path())
    # Progression accessible à tous les comptes (gratuit inclus)
    _update_streak(request.user)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    stats       = _get_or_create_stats(request.user)
    diag_scores  = {d.subject: d.score for d in DiagnosticResult.objects.filter(user=request.user)}
    quiz_sessions = QuizSession.objects.filter(user=request.user).order_by('-completed_at')[:10]

    # Optimized bulk calculation + scores unifiés
    all_blended = _compute_all_blended_scores(request.user)
    _prog_user_serie_subjects = list(SERIES.get(profile.serie or 'SVT', SERIES['SVT'])['subjects'].keys())
    _prog_scores = get_scores_for_user(request.user, set(_prog_user_serie_subjects), diag_scores)

    mats_extended = {}
    for k, v in MATS.items():
        sc = all_blended.get(k, {})
        mats_extended[k] = dict(v)
        mats_extended[k]['sessions']    = sc.get('quiz_count', 0)
        mats_extended[k]['exo_count']   = sc.get('exo_total', 0)
        blended = _prog_scores.get(k) if k in _prog_scores else sc.get('blended')
        mats_extended[k]['quiz_score']  = blended
        mats_extended[k]['quiz_avg']    = sc.get('quiz_avg')
        mats_extended[k]['exo_pct']     = sc.get('exo_pct')
        mats_extended[k]['has_course']  = sc.get('has_course', False)

    heures_etude = stats.minutes_etude // 60
    minutes_rest = stats.minutes_etude % 60

    blended_scores = [v for v in _prog_scores.values() if v is not None]
    avg_blended = round(sum(blended_scores) / len(blended_scores)) if blended_scores else 0
    _prog_serie_key = profile.serie or 'SVT'
    bac_score = estimate_bac_score(_prog_scores, _prog_serie_key, SERIES)

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
        study_recs = []
        for r in get_study_recommendations(request.user):
            if r['subject'] not in _serie_subjs_set:
                continue
            reason = r.get('reason', '')
            if 'topics à retravailler' in reason:
                parts = reason.split('topics à retravailler :', 1)
                if len(parts) > 1:
                    topics = [
                        _clean_topic_name(t.strip())
                        for t in parts[1].split(',')
                        if t.strip() and not _is_generic_topic_name(_clean_topic_name(t.strip()))
                    ]
                    if topics:
                        reason = f'{parts[0].strip()} — {", ".join(topics[:3])}'
            study_recs.append({**r, 'reason': reason})

        mastery_display = []
        for m in masteries:
            label = MATS.get(m.subject, {}).get('label', m.subject.title())
            weak = [
                _clean_topic_name(t) for t in (m.weak_topics or [])
                if _clean_topic_name(t) and not _is_generic_topic_name(_clean_topic_name(t))
            ]
            acc = m.correct_count + m.error_count
            if acc <= 0:
                continue
            mastery_display.append({
                'subject': m.subject,
                'label': label,
                'color': MATS.get(m.subject, {}).get('color', '#6366f1'),
                'score': _prog_scores.get(m.subject, int(round(m.mastery_score))),
                'level': m.confidence_level,
                'correct': m.correct_count,
                'total': acc,
                'weak_topics': weak[:3],
            })

        from .revision_planner import build_study_insights
        study_insights = build_study_insights(request.user, MATS, _get_user_serie_subjects, limit=10)
        if not study_insights:
            weak = sorted(
                ((s, sc) for s, sc in _prog_scores.items() if sc is not None),
                key=lambda x: x[1],
            )[:5]
            study_insights = []
            for subj, sc in weak:
                info = MATS.get(subj, {})
                study_insights.append({
                    'subject': subj,
                    'label': info.get('label', subj),
                    'score': int(sc),
                    'priority': 'haute' if sc < 55 else ('moyenne' if sc < 70 else 'basse'),
                    'chapter_num': 1,
                    'chapter': 'Révision prioritaire',
                    'subtopics': ['Quiz ciblés', 'Cours'],
                    'weakness_reason': f'Score actuel {int(sc)}% — à renforcer.',
                    'cours_url': f'/dashboard/cours/?subject={subj}',
                    'quiz_url': f'/dashboard/quiz/?subject={subj}',
                    'exo_url': f'/dashboard/exercices/?subject={subj}',
                    'quiz_category': '',
                })
        if not mastery_display and _prog_scores:
            mastery_display = [
                {
                    'label': MATS.get(subj, {}).get('label', subj),
                    'color': MATS.get(subj, {}).get('color', '#6366f1'),
                    'score': int(sc),
                    'level': 'en progrès' if sc < 70 else 'solide',
                    'correct': max(1, int(sc) // 10),
                    'total': 10,
                    'weak_topics': ['Révision'],
                }
                for subj, sc in list(_prog_scores.items())[:6]
                if sc is not None
            ]
        context['masteries']     = masteries
        context['mastery_display'] = mastery_display
        context['chat_summaries'] = chat_summaries
        context['study_recs']    = study_recs
        context['study_insights'] = study_insights
    except Exception:
        context['masteries']      = []
        context['mastery_display'] = []
        context['chat_summaries'] = []
        context['study_recs']     = []
        context['study_insights'] = []

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
        raw_coach = (request.POST.get('coach_name') or '').strip()
        if raw_coach:
            profile.coach_name = raw_coach[:40]
            profile.save(update_fields=['coach_name'])
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

    return render(request, 'core/profil.html', {
        'profile': profile,
        'stats': stats,
    })


def gains_view(request):
    if _is_guest(request) or not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    stats = _get_or_create_stats(request.user)
    from accounts.referrals import ensure_invite_code
    from accounts.models import StudentReferral, XpWithdrawal
    from core.xp_config import MIN_WITHDRAWAL_HTG, REFERRAL_REWARD_HTG, htg_to_xp, xp_to_htg
    invite_code = ensure_invite_code(profile)
    invite_link = request.build_absolute_uri(f'/?ref={invite_code}')
    my_xp = _user_xp(request.user, stats)
    htg_value = xp_to_htg(my_xp)
    referrals = (
        StudentReferral.objects.filter(referrer=request.user)
        .select_related('referred_user')
        .order_by('-created_at')[:30]
    )
    wd_qs = XpWithdrawal.objects.filter(user=request.user)
    pending_wd = wd_qs.filter(status='pending').first()
    return render(request, 'core/gains.html', {
        'active_page': 'gains',
        'profile': profile,
        'invite_code': invite_code,
        'invite_link': invite_link,
        'referral_reward_htg': REFERRAL_REWARD_HTG,
        'referral_reward_xp': htg_to_xp(REFERRAL_REWARD_HTG),
        'referrals': referrals,
        'paid_referrals': sum(1 for r in referrals if r.paid),
        'my_xp': my_xp,
        'xp_htg_value': htg_value,
        'min_withdraw_htg': MIN_WITHDRAWAL_HTG,
        'min_withdraw_xp': htg_to_xp(MIN_WITHDRAWAL_HTG),
        'withdrawals': list(wd_qs[:12]),
        'pending_withdrawal': pending_wd,
        'default_moncash': profile.phone or '',
    })


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


@login_required
@require_POST
def api_xp_withdraw(request):
    """Demande de retrait XP → MonCash."""
    data, err = _parse_json_body(request)
    if err:
        return err
    from core.xp import request_xp_withdrawal, get_user_xp
    from core.xp_config import MIN_WITHDRAWAL_HTG, htg_to_xp
    w, reason = request_xp_withdrawal(
        request.user,
        data.get('htg'),
        data.get('moncash') or '',
    )
    messages = {
        'invalid_amount': 'Montant invalide.',
        'below_minimum': f'Minimum {MIN_WITHDRAWAL_HTG} G.',
        'rate_unset': 'Conversion XP indisponible pour le moment.',
        'invalid_phone': 'Numéro MonCash invalide.',
        'pending_exists': 'Tu as déjà une demande en attente.',
        'insufficient': 'Solde XP insuffisant.',
        'duplicate': 'Cette demande existe déjà.',
    }
    if reason != 'ok' or w is None:
        return JsonResponse({'ok': False, 'error': messages.get(reason, 'Impossible de retirer.')}, status=400)
    return JsonResponse({
        'ok': True,
        'id': w.pk,
        'htg': w.amount_htg,
        'xp': w.xp_amount,
        'balance': get_user_xp(request.user),
        'min_xp': htg_to_xp(MIN_WITHDRAWAL_HTG),
    })


# ─────────────────────────────────────────────
# HISTORIQUE DES CONVERSATIONS
# ─────────────────────────────────────────────
@login_required
def historique_view(request):
    """Ancienne page historique : tout est dans le panneau du chat."""
    from urllib.parse import urlencode
    target = reverse('chat')
    q = (request.GET.get('q') or '').strip()
    params = {'hist': '1'}
    if q:
        params['hist_q'] = q
    return redirect(f'{target}?{urlencode(params)}')


@login_required
def conversation_detail(request, session_key):
    """Ouvre la conversation dans Astra."""
    exists = ChatMessage.objects.filter(user=request.user, session_key=session_key).exists()
    if not exists:
        return redirect('chat')
    return redirect(f"{reverse('chat')}?session={session_key}")


# ─────────────────────────────────────────────
# FICHES MÉMO (FLASHCARDS)
# ─────────────────────────────────────────────
def fiches_view(request):
    from django.utils import timezone
    subject = request.GET.get('subject', 'maths')

    # ── Premium gate (RETIRED: Now unlocked for free users) ──
    # if request.user.is_authenticated:
    #     from core.premium import is_premium
    #     if not is_premium(request.user):
    #         profile, _ = UserProfile.objects.get_or_create(user=request.user)
    #         return render(request, 'core/premium_required.html', {
    #             'profile': profile,
    #             'feature': 'Fiches Mémo',
    #             'message': 'Les fiches mémo sont réservées aux abonnés premium.',
    #         })

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

    # Load existing flashcards for this subject (Limit to 100 for performance)
    flashcards = list(Flashcard.objects.filter(subject=subject).order_by('-id')[:100])

    # Get user progress for these cards
    progress_qs = FlashcardProgress.objects.filter(
        user=request.user, flashcard__subject=subject
    ).select_related('flashcard')
    progress_map = {p.flashcard_id: p.status for p in progress_qs}

    # Stats (calculated on full queryset for accuracy, but view only shows subset)
    known  = FlashcardProgress.objects.filter(user=request.user, flashcard__subject=subject, status='known').count()
    review = FlashcardProgress.objects.filter(user=request.user, flashcard__subject=subject, status='review').count()

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
    user_profile = gemini.build_user_learning_profile_short(request.user)
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
# PLAN DE RÉVISION
# ─────────────────────────────────────────────

def _current_week_start():
    from datetime import date, timedelta
    today = date.today()
    return today - timedelta(days=today.weekday())


def _ensure_weekly_revision_plan(user, serie_key: str):
    """Crée un plan de la semaine si absent ou obsolète (nouvelle semaine calendaire)."""
    week_start = _current_week_start()
    latest = RevisionPlan.objects.filter(user=user).order_by('-created_at').first()
    if latest:
        created_local = _timezone.localtime(latest.created_at).date()
        if created_local >= week_start:
            return latest

    from .revision_planner import build_revision_plan
    plan_data = build_revision_plan(
        user, serie_key, 1, MATS, _get_user_serie_subjects,
    )
    if not plan_data:
        plan_data = {
            'summary': 'Plan de la semaine généré automatiquement selon tes résultats récents.',
            'weeks': [],
        }
    plan_data.setdefault('meta', {})
    plan_data['meta']['week_start'] = week_start.isoformat()
    plan_data['meta']['auto'] = True
    return RevisionPlan.objects.create(
        user=user,
        serie=serie_key,
        content=plan_data,
        completed_tasks=[],
    )


def plan_view(request):
    # ── Premium gate (RETIRED: Now unlocked for free users) ──
    # if request.user.is_authenticated:
    #     from core.premium import is_premium
    #     if not is_premium(request.user):
    #         profile, _ = UserProfile.objects.get_or_create(user=request.user)
    #         return render(request, 'core/premium_required.html', {
    #             'profile': profile,
    #             'feature': 'Plan de révision',
    #             'message': 'Le plan de révision est réservé aux abonnés premium.',
    #         })

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
    try:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        try:
            serie_key = profile.serie or 'SVT'
        except Exception:
            serie_key = 'SVT'
        latest = _ensure_weekly_revision_plan(request.user, serie_key)
        plans = RevisionPlan.objects.filter(user=request.user)[:5]
        from .learning_tracker import build_combined_weakness_scores
        diag_scores = build_combined_weakness_scores(request.user)
        weakness_items = sorted(
            [{'key': k, 'label': MATS.get(k, {}).get('label', k), 'score': v}
             for k, v in diag_scores.items()],
            key=lambda x: x['score'],
        )
        week_start = _current_week_start()
        return render(request, 'core/plan.html', {
            'plans':       plans,
            'latest_plan': latest,
            'mats':        MATS,
            'diag_scores': diag_scores,
            'weakness_items': weakness_items,
            'completed_tasks_json': json.dumps(list(latest.completed_tasks or []) if latest else []),
            'profile':     profile,
            'week_start': week_start,
        })
    except Exception as _e:
        _logger.exception('plan_view error')
        return render(request, 'core/plan.html', {
            'plans': [], 'latest_plan': None, 'mats': MATS, 'diag_scores': {}, 'profile': None,
            'error_msg': "Une erreur temporaire s'est produite. Réessaie dans quelques instants.",
        })


@login_required
@require_POST
def api_generate_plan(request):
    """Régénère le plan de la semaine (1 semaine, lacunes récentes)."""
    try:
        data, _err = _parse_json_body(request)
        if _err:
            return _err
        weeks = 1

        try:
            serie_key = request.user.profile.serie or 'SVT'
        except Exception:
            serie_key = 'SVT'

        from .learning_tracker import build_combined_weakness_scores
        combined_scores = build_combined_weakness_scores(request.user)
        diag_scores = combined_scores

        from .revision_planner import build_revision_plan

        plan_data = build_revision_plan(
            request.user,
            serie_key,
            weeks,
            MATS,
            _get_user_serie_subjects,
        )

        if not plan_data:
            _user_subjs = list(SERIES.get(serie_key, SERIES['SVT'])['subjects'].keys())
            _fallback_days_pool = []
            for _si, _subj in enumerate(_user_subjs):
                _lbl = MATS.get(_subj, {}).get('label', _subj)
                _prio = 'high' if diag_scores.get(_subj, 50) < 50 else ('medium' if diag_scores.get(_subj, 50) < 70 else 'low')
                _fallback_days_pool.append({'day': ['Lundi','Mardi','Mercredi','Jeudi','Vendredi'][_si % 5], 'subject': _lbl, 'task': f'Réviser {_lbl} — chapitres clés', 'duration_min': 60, 'priority': _prio})
            _wdays = [_fallback_days_pool[d % len(_fallback_days_pool)] for d in range(5)]
            for i, d in enumerate(_wdays):
                d['day'] = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi'][i]
            plan_data = {
                'summary': 'Plan de la semaine généré automatiquement.',
                'weeks': [{'label': 'Cette semaine', 'focus': 'Révision ciblée', 'days': _wdays}],
            }

        week_start = _current_week_start()
        plan_data.setdefault('meta', {})
        plan_data['meta']['week_start'] = week_start.isoformat()
        plan_data['meta']['auto'] = True

        plan = RevisionPlan.objects.create(
            user=request.user,
            serie=serie_key,
            content=plan_data,
            completed_tasks=[],
        )
        return JsonResponse({'ok': True, 'plan_id': plan.id, 'plan': plan_data})
    except Exception as e:
        _logger.exception('api_generate_plan error')
        return JsonResponse({'ok': False, 'error': "L'IA est momentanément indisponible."})


@login_required
@require_POST
def api_plan_progress(request):
    """Sauvegarde la progression des tâches du plan de révision (serveur)."""
    try:
        data, _err = _parse_json_body(request)
        if _err:
            return _err
        plan_id = data.get('plan_id')
        task_id = (data.get('task_id') or '').strip()
        done = bool(data.get('done', True))
        if not plan_id or not task_id:
            return JsonResponse({'ok': False, 'error': 'plan_id et task_id requis'}, status=400)
        plan = RevisionPlan.objects.get(pk=plan_id, user=request.user)
        tasks = list(plan.completed_tasks or [])
        if done:
            if task_id not in tasks:
                tasks.append(task_id)
        else:
            tasks = [t for t in tasks if t != task_id]
        plan.completed_tasks = tasks
        plan.save(update_fields=['completed_tasks'])
        return JsonResponse({'ok': True, 'completed_tasks': tasks})
    except RevisionPlan.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Plan introuvable'}, status=404)
    except Exception:
        _logger.exception('api_plan_progress error')
        return JsonResponse({'ok': False, 'error': 'Erreur serveur'}, status=500)


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

    user_profile = gemini.build_user_learning_profile_short(request.user)
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


def _exercise_fingerprint(ex: dict) -> str:
    intro = (ex.get('intro') or ex.get('enonce') or '')[:800]
    qs = '|'.join(str(q)[:200] for q in (ex.get('questions') or [])[:12])
    return hashlib.md5(f"{intro}\n{qs}".encode('utf-8', errors='ignore')).hexdigest()


def _exercise_session_public(row: ExerciseSession, include_payload: bool = False) -> dict:
    data = {
        'id': row.id,
        'subject': row.subject,
        'chapter': row.chapter,
        'chapter_id': row.chapter_id,
        'title': row.title or row.chapter or row.subject,
        'preview': row.preview,
        'status': row.status,
        'is_favorite': row.is_favorite,
        'msg_count': len(row.messages or []),
        'updated_at': row.updated_at.isoformat() if row.updated_at else '',
    }
    if include_payload:
        data['exercise'] = row.exercise or {}
        data['messages'] = row.messages or []
        data['session_state'] = row.session_state or {}
    return data


@login_required
@require_GET
def api_exercise_sessions(request):
    qs = ExerciseSession.objects.filter(user=request.user)
    subject = (request.GET.get('subject') or '').strip()
    if subject:
        qs = qs.filter(subject=subject)
    if request.GET.get('favorites') == '1':
        qs = qs.filter(is_favorite=True)
    rows = list(qs[:50])
    return JsonResponse({'ok': True, 'sessions': [_exercise_session_public(r) for r in rows]})


@login_required
@require_GET
def api_exercise_session_detail(request, pk: int):
    row = ExerciseSession.objects.filter(user=request.user, pk=pk).first()
    if not row:
        return JsonResponse({'error': 'Session introuvable.'}, status=404)
    return JsonResponse({'ok': True, 'session': _exercise_session_public(row, include_payload=True)})


@login_required
@require_POST
def api_exercise_session_save(request):
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    exercise = data.get('exercise') or {}
    if not isinstance(exercise, dict) or not (exercise.get('intro') or exercise.get('enonce')):
        return JsonResponse({'error': 'Exercice manquant.'}, status=400)
    exo_hash = _exercise_fingerprint(exercise)
    title = (data.get('title') or exercise.get('theme') or data.get('subject') or 'Exercice')[:220]
    preview = (data.get('preview') or exercise.get('intro') or exercise.get('enonce') or '')
    preview = re.sub(r'\s+', ' ', str(preview)).strip()[:280]
    defaults = {
        'subject': (data.get('subject') or '')[:50],
        'chapter': (data.get('chapter') or '')[:200],
        'chapter_id': str(data.get('chapter_id') or '')[:40],
        'title': title,
        'preview': preview,
        'exercise': exercise,
        'messages': data.get('messages') if isinstance(data.get('messages'), list) else [],
        'session_state': data.get('session_state') if isinstance(data.get('session_state'), dict) else {},
        'status': data.get('status') if data.get('status') in ('active', 'completed') else 'active',
    }
    if 'is_favorite' in data:
        defaults['is_favorite'] = bool(data.get('is_favorite'))
    row, _created = ExerciseSession.objects.update_or_create(
        user=request.user,
        exercise_hash=exo_hash,
        defaults=defaults,
    )
    return JsonResponse({'ok': True, **_exercise_session_public(row)})


@login_required
@require_POST
def api_exercise_session_favorite(request, pk: int):
    row = ExerciseSession.objects.filter(user=request.user, pk=pk).first()
    if not row:
        return JsonResponse({'error': 'Session introuvable.'}, status=404)
    data, _err = _parse_json_body(request)
    if _err:
        data = {}
    if 'is_favorite' in (data or {}):
        row.is_favorite = bool(data.get('is_favorite'))
    else:
        row.is_favorite = not row.is_favorite
    row.save(update_fields=['is_favorite', 'updated_at'])
    return JsonResponse({'ok': True, **_exercise_session_public(row)})


def bookmarks_view(request):
    # ── Premium gate (RETIRED: Now unlocked for free users) ──
    # if request.user.is_authenticated:
    #     from core.premium import is_premium
    #     if not is_premium(request.user):
    #         profile, _ = UserProfile.objects.get_or_create(user=request.user)
    #         return render(request, 'core/premium_required.html', {
    #             'profile': profile,
    #             'feature': 'Favoris',
    #             'message': 'Les favoris sont réservés aux abonnés premium.',
    #         })

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
        return JsonResponse({
            'timeline': fake_timeline,
            'radar': radar,
            'fc_progress': {},
            'subject_evolution': [
                {
                    'subject': subj,
                    'label': MATS.get(subj, {}).get('label', subj),
                    'color': MATS.get(subj, {}).get('color', '#6366f1'),
                    'icon': MATS.get(subj, {}).get('icon', 'fa-book'),
                    'current': sc,
                    'trend': 5 if sc >= 60 else -3,
                    'quiz_count': 3 + (sc % 4),
                    'points': [
                        {'date': '01/03', 'pct': max(30, sc - 12)},
                        {'date': '08/03', 'pct': max(35, sc - 5)},
                        {'date': '15/03', 'pct': sc},
                    ],
                }
                for subj, sc in _GUEST_DEMO['quiz_scores'].items()
                if subj in _GUEST_DEMO['user_serie_subjects']
            ],
        })
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
        'date':    _local_time(s.completed_at).strftime('%d/%m'),
        'subject': s.subject,
        'pct':     s.get_percentage(),
    } for s in sessions_qs]

    # Radar chart data — scores unifiés (identique dashboard / progression)
    diag_for_radar = {d.subject: d.score for d in DiagnosticResult.objects.filter(user=request.user)}
    unified_scores = get_scores_for_user(request.user, user_subjs, diag_for_radar)
    radar = []
    for subj, info in MATS.items():
        if subj not in user_subjs:
            continue
        radar.append({'subject': info['label'], 'score': unified_scores.get(subj, 0)})

    # Flashcards progress
    fc_progress = {}
    for subj in MATS:
        total  = Flashcard.objects.filter(subject=subj).count()
        known  = FlashcardProgress.objects.filter(
            user=request.user, flashcard__subject=subj, status='known'
        ).count()
        fc_progress[subj] = {'total': total, 'known': known}

    # Évolution par matière (derniers quiz par sujet)
    sessions_all = list(
        QuizSession.objects.filter(user=request.user)
        .order_by('completed_at')
        .values('subject', 'score', 'total', 'completed_at')[:200]
    )
    by_subject: dict = {}
    for row in sessions_all:
        subj = row['subject']
        if subj not in user_subjs:
            continue
        if row['total'] and row['total'] > 0:
            pct = round((row['score'] / row['total']) * 100)
            entry = by_subject.setdefault(subj, {
                'label': MATS.get(subj, {}).get('label', subj),
                'color': MATS.get(subj, {}).get('color', '#6366f1'),
                'icon': MATS.get(subj, {}).get('icon', 'fa-book'),
                'points': [],
            })
            if len(entry['points']) < 12:
                entry['points'].append({
                    'date': _local_time(row['completed_at']).strftime('%d/%m'),
                    'pct': pct,
                })

    all_blended = _compute_all_blended_scores(request.user)
    for subj in user_subjs:
        if subj not in by_subject:
            sc = all_blended.get(subj, {})
            blended = sc.get('blended')
            if blended is not None:
                by_subject[subj] = {
                    'label': MATS.get(subj, {}).get('label', subj),
                    'color': MATS.get(subj, {}).get('color', '#6366f1'),
                    'icon': MATS.get(subj, {}).get('icon', 'fa-book'),
                    'points': [{'date': '—', 'pct': blended}],
                }

    subject_evolution = []
    for subj in user_subjs:
        info = by_subject.get(subj)
        if not info or not info['points']:
            diag = DiagnosticResult.objects.filter(user=request.user, subject=subj).first()
            if diag:
                info = {
                    'label': MATS.get(subj, {}).get('label', subj),
                    'color': MATS.get(subj, {}).get('color', '#6366f1'),
                    'icon': MATS.get(subj, {}).get('icon', 'fa-book'),
                    'points': [{'date': 'diag', 'pct': int(diag.score)}],
                }
            else:
                # Fallback scores unifiés (évite page progression vide)
                blended = unified_scores.get(subj)
                if blended is None:
                    continue
                info = {
                    'label': MATS.get(subj, {}).get('label', subj),
                    'color': MATS.get(subj, {}).get('color', '#6366f1'),
                    'icon': MATS.get(subj, {}).get('icon', 'fa-book'),
                    'points': [{'date': '—', 'pct': int(blended)}],
                }
        pts = info['points']
        current = pts[-1]['pct']
        trend = 0
        if len(pts) >= 4:
            recent = sum(p['pct'] for p in pts[-2:]) / 2
            older = sum(p['pct'] for p in pts[-4:-2]) / 2
            trend = round(recent - older)
        elif len(pts) >= 2:
            trend = pts[-1]['pct'] - pts[0]['pct']
        subject_evolution.append({
            'subject': subj,
            'label': info['label'],
            'color': info['color'],
            'icon': info['icon'],
            'current': current,
            'trend': trend,
            'quiz_count': len(pts),
            'points': pts,
        })
    subject_evolution.sort(key=lambda x: x['current'])

    return JsonResponse({
        'timeline':    timeline,
        'radar':       radar,
        'fc_progress': fc_progress,
        'subject_evolution': subject_evolution,
    })


# ─────────────────────────────────────────────
# COACHING IA — analyse complète + conseils
# ─────────────────────────────────────────────

def _local_smart_coach_fallback(user) -> dict:
    """Coach sans IA — insights depuis coaching_context + lacunes chapitres."""
    from .revision_planner import build_study_insights
    name = user.first_name or user.username
    insights = build_study_insights(user, MATS, _get_user_serie_subjects, limit=5)
    lines = [f'<strong>{name}</strong>, voici ton bilan personnalisé (sans appel IA) :']
    if insights:
        for ins in insights[:4]:
            ch = ins.get('chapter', '')
            reason = (ins.get('weakness_reason') or '')[:140]
            lines.append(
                f'• <strong>{ins["label"]}</strong> ({ins["score"]}%) — '
                f'Ch.{ins.get("chapter_num", "")} {ch}. {reason}'
            )
        lines.append('Concentre-toi sur ces chapitres cette semaine, puis régénère ton plan de révision.')
    else:
        lines.append('Passe des quiz pour que je puisse analyser tes lacunes précises.')
    quiz_picks = []
    for ins in insights[:3]:
        subj = ins['subject']
        info = MATS.get(subj, {})
        cat = ins.get('quiz_category') or 'Révision'
        quiz_picks.append({
            'subject': subj,
            'subject_label': info.get('label', subj),
            'subject_color': info.get('color', '#6366f1'),
            'category': cat,
            'reason': (ins.get('weakness_reason') or f'Score {ins.get("score", 0)}%')[:200],
            'quiz_url': ins.get('quiz_url') or f'/dashboard/quiz/?subject={subj}',
            'n_questions': 8,
            'questions': [],
        })
    chapter_recs = []
    for ins in insights[:3]:
        subj = ins['subject']
        info = MATS.get(subj, {})
        chapter_recs.append({
            'subject': subj,
            'subject_label': info.get('label', subj),
            'subject_color': info.get('color', '#06b6d4'),
            'chapter': ins.get('chapter') or 'Chapitre prioritaire',
            'reason': ' · '.join(ins.get('subtopics', [])[:2]) or (ins.get('weakness_reason') or '')[:160],
            'url': ins.get('cours_url') or f'/dashboard/cours/?subject={subj}',
        })
    exercise_recs = []
    for ins in insights[:2]:
        subj = ins['subject']
        info = MATS.get(subj, {})
        exercise_recs.append({
            'subject': subj,
            'subject_label': info.get('label', subj),
            'subject_color': info.get('color', '#8b5cf6'),
            'chapter': ins.get('chapter') or 'Exercices ciblés',
            'reason': (ins.get('weakness_reason') or 'À consolider')[:160],
            'url': ins.get('exo_url') or f'/dashboard/exercices/?subject={subj}',
        })
    return {
        'message': '<br>'.join(lines),
        'message_html': '<br>'.join(lines),
        'quiz_picks': quiz_picks,
        'exercise_recs': exercise_recs,
        'chapter_recs': chapter_recs,
    }


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
    from core.coaching_context import build_coaching_context, append_hyper_coaching_cards

    cards = []

    user_subjs = _get_user_serie_subjects(user)
    serie_mats = {k: v for k, v in MATS.items() if not user_subjs or k in user_subjs}

    # ── Contexte riche (quiz wrong answers, cours, SM-2, diagnostic, chat…) ──
    ctx = build_coaching_context(user, MATS, _get_user_serie_subjects)
    profile = ctx['profile']
    stats = ctx['stats']
    today = ctx['today']
    mastery_map = ctx['mastery_map']
    memories = ctx['memories']
    due_by_subj = ctx['due_by_subj']
    total_due = ctx['total_due']
    total_mistakes = ctx['total_mistakes']
    total_mastered = ctx['total_mastered']
    topic_error_counts = ctx['topic_error_counts']
    sessions_by_subj = ctx['sessions_by_subj']

    # Scores blended quiz + exo (complète le contexte mastery)
    all_blended = _compute_all_blended_scores(user)
    subject_data = {}
    for subj, info in MATS.items():
        sc = all_blended.get(subj, {})
        sessions = sessions_by_subj.get(subj, [])
        pcts = [round((s.score / s.total) * 100) for s in sessions if s.total]
        trend = 0
        if len(pcts) >= 4:
            recent_avg = sum(pcts[:3]) / 3
            older_avg  = sum(pcts[3:]) / max(1, len(pcts[3:]))
            trend = round(recent_avg - older_avg)
        last_session_date = sessions[0].completed_at.date() if sessions else None
        blended = sc.get('blended') if sc.get('blended') is not None else 0
        subject_data[subj] = {
            'avg':   blended,
            'quiz_avg': sc.get('quiz_avg'),
            'trend': trend,
            'count': sc.get('quiz_count', 0),
            'last':  last_session_date,
            'pcts':  pcts,
            'exo_total': sc.get('exo_total', 0),
            'exo_pct':   sc.get('exo_pct'),
            'has_course': sc.get('has_course', False),
        }

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
            topics_in_5 = [e.get('topic', '') for e in recent5 if e.get('topic')]
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
                f'{len(recent5)} erreur{"s" if len(recent5)>1 else ""} récente{"s" if len(recent5)>1 else ""} enregistrée{"s" if len(recent5)>1 else ""} sur cette matière.'
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

    # ── Cartes ultra-ciblées depuis le contexte complet ───────────────
    try:
        cards = append_hyper_coaching_cards(
            cards, ctx, MATS,
            {
                '_pick_clear_topic': _pick_clear_topic,
                '_clean_topic_name': _clean_topic_name,
                '_has_resources': _has_resources,
                'get_subject_chapters': get_subject_chapters if _has_resources else None,
            },
        )
    except Exception as _ctx_err:
        print(f"[COACHING_CTX] append_hyper: {_ctx_err}")

    # ── Trier par priorité + limiter à 6 cartes ───────────────────────
    cards.sort(key=lambda c: c['priority'])
    return cards[:6]



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
        cards = _guest_platform_coaching_cards()
        if guest_serie:
            cards = _filter_coaching_cards_by_serie(cards, guest_serie)
        return JsonResponse({'ok': True, 'cards': cards})
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'cards': []}, status=401)
    cards = _generate_coaching_cards(request.user)
    return JsonResponse({'ok': True, 'cards': cards})


def _ai_progress_cache_valid_today(cache):
    """Cache IA progression : valide pour le jour calendaire courant."""
    from django.utils import timezone as _tz
    if not cache or not cache.is_valid or not cache.last_updated:
        return False
    return cache.last_updated.date() == _tz.localdate()


def api_coaching(request):
    """Retourne les cartes de coaching + un message IA personnalisé pour la page Progression."""
    if _is_guest(request):
        g = _GUEST_DEMO
        return JsonResponse({
            'ok': True,
            'cards': _guest_platform_coaching_cards(),
            'advice': g['coach_advice'],
            'chapter_advice': 'Priorité démo : Économie (comptabilité nationale) puis SVT (génétique).',
        })
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'cards': [], 'advice': '', 'chapter_advice': ''}, status=401)
    from core.premium import is_premium
    if not is_premium(request.user):
        return JsonResponse({
            'ok': False, 'premium_required': True,
            'error': 'Le coaching avancé est réservé aux abonnés premium.',
            'cards': [], 'advice': '', 'chapter_advice': '',
        }, status=403)
    from datetime import date
    from .models import MistakeTracker, AIMemory
    from django.db.models import Sum

    user  = request.user
    today = date.today()
    cards = _generate_coaching_cards(user)

    # ── Collecte des données élève pour le prompt IA ──────────────────
    profile, _ = UserProfile.objects.get_or_create(user=user)
    stats       = _get_or_create_stats(user)

    # Use blended scores (Optimized bulk calculation)
    all_blended = _compute_all_blended_scores(user)
    
    # Pre-fetch recent sessions for all subjects to avoid N+1
    all_recent_sessions = list(QuizSession.objects.filter(user=user).order_by('-completed_at')[:200])
    sessions_by_subj = {s: [] for s in MATS}
    for rs in all_recent_sessions:
        if rs.subject in sessions_by_subj and len(sessions_by_subj[rs.subject]) < 15:
            sessions_by_subj[rs.subject].append(rs)

    subject_data = {}
    for subj, info in MATS.items():
        sc = all_blended.get(subj, {})
        sessions = sessions_by_subj[subj]
        pcts = [round((s.score / s.total) * 100) for s in sessions if s.total]
        trend = 0
        if len(pcts) >= 4:
            recent_avg = sum(pcts[:3]) / 3
            older_avg  = sum(pcts[3:]) / max(1, len(pcts[3:]))
            trend = round(recent_avg - older_avg)
        last_date = sessions[0].completed_at.date() if sessions else None
        subject_data[subj] = {
            'label':      info['label'],
            'avg':        sc.get('blended') if sc.get('blended') is not None else 0,
            'quiz_avg':   sc.get('quiz_avg'),
            'trend':      trend,
            'count':      sc.get('quiz_count', 0),
            'last':       last_date,
            'exo_total':  sc.get('exo_total', 0),
            'exo_pct':    sc.get('exo_pct'),
            'has_course': sc.get('has_course', False),
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
    
    # Vérifier si le cache est encore valide (1 refresh par jour calendaire)
    is_cache_valid = _ai_progress_cache_valid_today(cache)
    
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
        g = _GUEST_DEMO
        quiz_picks = []
        for s, sc in g['weaknesses']:
            info = MATS.get(s, {})
            quiz_picks.append({
                'subject': s,
                'subject_label': info.get('label', s),
                'subject_color': info.get('color', '#6366f1'),
                'category': 'Révision ciblée',
                'reason': f'Score démo {sc}% — à renforcer avant le BAC.',
                'quiz_url': f'/dashboard/quiz/?subject={s}',
                'n_questions': 8,
                'questions': [],
            })
        chapter_recs = [
            {
                'subject': 'svt',
                'subject_label': MATS.get('svt', {}).get('label', 'SVT'),
                'subject_color': MATS.get('svt', {}).get('color', '#06b6d4'),
                'chapter': 'Division cellulaire',
                'reason': 'Méiose, mitose et lois de Mendel — point faible démo.',
                'url': '/dashboard/cours/?subject=svt',
            },
            {
                'subject': 'maths',
                'subject_label': MATS.get('maths', {}).get('label', 'Maths'),
                'subject_color': MATS.get('maths', {}).get('color', '#06b6d4'),
                'chapter': 'Dérivées',
                'reason': 'Fonctions composées et règles de dérivation.',
                'url': '/dashboard/cours/?subject=maths',
            },
        ]
        exercise_recs = [
            {
                'subject': 'svt',
                'subject_label': MATS.get('svt', {}).get('label', 'SVT'),
                'subject_color': MATS.get('svt', {}).get('color', '#8b5cf6'),
                'chapter': 'Génétique mendélienne',
                'reason': 'Point faible démo — exercices BAC recommandés.',
                'url': '/dashboard/exercices/?subject=svt',
            },
            {
                'subject': 'maths',
                'subject_label': MATS.get('maths', {}).get('label', 'Maths'),
                'subject_color': MATS.get('maths', {}).get('color', '#8b5cf6'),
                'chapter': 'Dérivées',
                'reason': 'À consolider — entraînement ciblé.',
                'url': '/dashboard/exercices/?subject=maths',
            },
        ]
        return JsonResponse({
            'ok': True,
            'message': g['coach_advice'].replace('<br>', '\n').replace('<strong>', '').replace('</strong>', '').replace('<em>', '').replace('</em>', ''),
            'message_html': g['coach_advice'],
            'quiz_picks': quiz_picks,
            'exercise_recs': exercise_recs,
            'chapter_recs': chapter_recs,
        })
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'auth required'}, status=401)

    user = request.user
    from core.premium import is_premium
    # Comptes gratuits : coach local (pas d'appel IA) pour que Progression ne soit pas vide
    if not is_premium(user):
        fallback = _local_smart_coach_fallback(user)
        return JsonResponse({'ok': True, 'premium': False, **fallback})

    from django.utils import timezone as _tz_sc
    from datetime import timedelta as _td_sc
    from .models import AIProgressCache
    if request.GET.get('refresh') != '1':
        try:
            _sc_cache = AIProgressCache.objects.filter(user=user).first()
            if (_sc_cache and _sc_cache.is_valid and _sc_cache.smart_coach_data
                    and _ai_progress_cache_valid_today(_sc_cache)):
                return JsonResponse({'ok': True, 'cached': True, **_sc_cache.smart_coach_data})
        except Exception:
            pass

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
        plan = _local_smart_coach_fallback(user)

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

    _sc_payload = {
        'message':      plan.get('message', ''),
        'quiz_picks':   quiz_picks_enriched,
        'chapter_recs': chapter_recs,
        'exercise_recs': exercise_recs,
    }
    try:
        _sc_store, _ = AIProgressCache.objects.get_or_create(user=user)
        _sc_store.smart_coach_data = _sc_payload
        _sc_store.is_valid = True
        _sc_store.save(update_fields=['smart_coach_data', 'is_valid', 'last_updated'])
    except Exception:
        pass

    return JsonResponse({'ok': True, **_sc_payload})


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
    due_count = MistakeTracker.objects.filter(
        user=request.user, mastered=False, next_review__lte=today,
    ).count()
    return JsonResponse({'mistakes': mistakes, 'due': due_count})


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
        g = _GUEST_DEMO
        demo_progress = g.get('course_progress', {})
        chapters_by_subject = {}
        for subj, info in MATS.items():
            if subj not in g['user_serie_subjects']:
                continue
            chapters = _get_cours_chapters(subj)
            base = int(demo_progress.get(subj, 40))
            for i, ch in enumerate(chapters):
                # Progression démo réaliste décroissante sur les chapitres
                ch['guest_locked'] = True
                ch['progress_pct'] = max(5, min(95, base + (8 - i * 7)))
            chapters_by_subject[subj] = {
                'info': info,
                'chapters': chapters,
                'count': len(chapters),
            }
        return render(request, 'core/cours.html', {
            'profile': None,
            'chapters_by_subject': chapters_by_subject,
            'mats': MATS,
            'user_serie_subjects': g['user_serie_subjects'],
            'any_chapters': any(d['count'] > 0 for d in chapters_by_subject.values()),
            'is_guest': True,
        })
    if not request.user.is_authenticated:
        return redirect('/login/?next=' + request.get_full_path())
    spa_mode = getattr(request, 'spa_mode', False)
    profile = UserProfile.objects.filter(user=request.user).only(
        'serie', 'langue_etrangere', 'coach_name', 'first_name', 'school',
    ).first()
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
    user_subjs = _get_user_serie_subjects(request.user)
    progress_by_chapter = _cours_progress_by_chapter(request.user, user_subjs)

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

    return render(request, 'core/cours.html', {
        'profile': profile,
        'chapters_by_subject': chapters_by_subject,
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
        hybrid_course = pdf_loader.get_hybrid_course_payload(subject, num, chapter_title)
        
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
        'messages_json': _json_mod.dumps(_visible_msgs).replace('</', r'<\/'),
        'progress_step': session.progress_step,
        'total_steps': _total_steps,
        'plan_tasks_json': _json_mod.dumps(_plan_tasks),
        'hybrid_mode': hybrid_mode,
        'hybrid_course_json': _json_mod.dumps(hybrid_course, ensure_ascii=False).replace('</', r'<\/'),
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
            _image_data, _image_mime = gemini.prepare_image_bytes(_image_data, _image_mime)
        except Exception:
            pass

    if not user_msg or not session_id:
        return JsonResponse({'error': 'Paramètres manquants'}, status=400)

    try:
        session = CourseSession.objects.get(pk=session_id, user=request.user)
    except CourseSession.DoesNotExist:
        return JsonResponse({'error': 'Session introuvable'}, status=404)

    incoming_course_step = int(session.progress_step or 0)

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
            hybrid_payload = pdf_loader.get_hybrid_course_payload(
                session.chapter_subject, session.chapter_num, chapter_title,
            )
            hybrid_total_steps = max(1, len(hybrid_payload.get('subchapters', [])))

        try:
            user_profile = gemini.build_user_learning_profile_short(request.user)
        except Exception:
            user_profile = ''

        _clar_lang = _get_user_lang(request)
        _clar_local = local_responses.try_local_chat_response(
            user_msg,
            subject=subj,
            user_lang=_clar_lang,
            subject_label=_get_subj_label(subj),
            chapter_title=chunk_title or subchapter_title or chapter_title,
            has_image=bool(_image_data),
        )
        if _clar_local:
            ts = _timezone.localtime(_timezone.now()).strftime('%H:%M')
            session.messages.append({'role': 'user', 'content': user_msg, 'ts': ts})
            session.messages.append({'role': 'assistant', 'content': _clar_local, 'ts': ts})
            session.progress_step = current_step
            session.save(update_fields=['messages', 'progress_step', 'updated_at'])
            return JsonResponse({
                'reply': _clar_local,
                'followups': [],
                'new_step': current_step,
                'total_steps': hybrid_total_steps,
                'task_list': [],
                'auto_continue': False,
                'local': True,
            })

        # Score maîtrise pour la matière
        _subject_score = None
        try:
            from .learning_tracker import get_subject_level_score
            _subject_score = get_subject_level_score(request.user, subj)
        except Exception:
            pass

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
                subject_score=_subject_score,
            )
        except Exception as _e:
            import logging as _logging
            _logging.getLogger(__name__).error('course_chunk_clarification error: %s', _e, exc_info=True)
            return JsonResponse({'error': f'Erreur IA : {type(_e).__name__}. Réessaie dans quelques secondes.'}, status=503)

        ts = _timezone.localtime(_timezone.now()).strftime('%H:%M')
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
            exam_excerpts, content_source_mode = _build_course_ai_context(
                session.chapter_subject,
                session.chapter_num,
                user_msg,
            )
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

    # Score maîtrise pour la matière
    _subject_score = None
    try:
        from .learning_tracker import get_subject_level_score
        _subject_score = get_subject_level_score(request.user, subj)
    except Exception:
        pass

    # ── Plan pédagogique — généré une fois, caché dans la session ──────────────────
    chapter_task_list = None
    plan_invalidated = False
    plan_version = 12  # static chapter plans — no runtime IA for task_list
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

    if not chapter_task_list:
        try:
            chapter_task_list = pdf_loader.get_chapter_plan(
                subj,
                session.chapter_num or 0,
                chapter_title=chapter_title,
            )
            if chapter_task_list:
                content_source_mode = 'static_plan'
                session.messages = [m for m in session.messages if m.get('role') != '__plan__']
                session.messages.insert(0, {
                    'role': '__plan__',
                    'chapter_title': chapter_title,
                    'plan_source': content_source_mode,
                    'plan_version': plan_version,
                    'content': json.dumps(chapter_task_list, ensure_ascii=False),
                })
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

    _is_auto_continue_msg = user_msg.strip() in ('[AUTO_CONTINUE]', '[REGEN_TRUNCATED]')
    if not _is_auto_continue_msg and not _image_data:
        _course_user_lang = _get_user_lang(request)
        _local_course = local_responses.try_local_chat_response(
            user_msg,
            subject=subj,
            user_lang=_course_user_lang,
            subject_label=_get_subj_label(subj),
            chapter_title=chapter_title,
            has_image=False,
        )
        if _local_course:
            ts = _timezone.localtime(_timezone.now()).strftime('%H:%M')
            session.messages.append({'role': 'user', 'content': user_msg, 'ts': ts})
            session.messages.append({'role': 'assistant', 'content': _local_course, 'ts': ts})
            if len(session.messages) > 40:
                meta_msgs = [m for m in session.messages if str(m.get('role', '')).startswith('__')]
                chat_msgs = [m for m in session.messages if not str(m.get('role', '')).startswith('__')]
                chat_msgs = chat_msgs[-COURSE_SESSION_CHAT_KEEP:]
                session.messages = meta_msgs + chat_msgs
            session.save(update_fields=['messages', 'updated_at'])
            return JsonResponse({
                'reply': _local_course,
                'followups': [],
                'new_step': session.progress_step,
                'total_steps': len(chapter_task_list) if chapter_task_list else 3,
                'task_list': chapter_task_list or [],
                'auto_continue': False,
                'local': True,
            })

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
            subject_score=_subject_score,
        )
    except Exception as _e:
        from core.ai_usage import AiBudgetExceeded, budget_exceeded_json
        if isinstance(_e, AiBudgetExceeded):
            return JsonResponse(budget_exceeded_json(_e.reason), status=429)
        import logging as _logging
        _logging.getLogger(__name__).error('course_chat error: %s', _e, exc_info=True)
        return JsonResponse({'error': f'Erreur IA : {type(_e).__name__}. Réessaie dans quelques secondes.'}, status=503)

    if not result.get('reply'):
        return JsonResponse({'error': "L'IA n'a pas pu générer de réponse. Réessaie."})

    ts = _timezone.localtime(_timezone.now()).strftime('%H:%M')
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

    if len(session.messages) > 40:
        meta_msgs = [m for m in session.messages if str(m.get('role', '')).startswith('__')]
        chat_msgs = [m for m in session.messages if not str(m.get('role', '')).startswith('__')]
        chat_msgs = chat_msgs[-COURSE_SESSION_CHAT_KEEP:]
        session.messages = meta_msgs + chat_msgs

    session.save(update_fields=['messages', 'progress_step', 'updated_at'])

    xp_gained = 0
    try:
        from core.xp import grant_course_completion
        total_steps = int(result.get('total_steps') or (len(chapter_task_list) if chapter_task_list else 0) or 0)
        cres = grant_course_completion(
            request.user, session, incoming_course_step, int(result['new_step'] or 0), total_steps,
        )
        if cres.granted:
            xp_gained = cres.amount
    except Exception:
        pass

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
        'xp_gained':     xp_gained,
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

    if session.chapter_num is None:
        return JsonResponse({'error': 'Mode hybride indisponible pour cette matière.'}, status=400)

    hybrid_payload = pdf_loader.get_hybrid_course_payload(
        session.chapter_subject, session.chapter_num,
        session.chapter_title or '',
    )
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

    prev_step = session.progress_step or 0
    CourseProgressState.objects.update_or_create(
        user=request.user,
        course_key=_hybrid_course_key(session.chapter_subject, session.chapter_num),
        defaults={'state': {'subchapter_idx': subchapter_idx, 'chunk_idx': chunk_idx}},
    )
    session.progress_step = subchapter_idx
    session.save(update_fields=['progress_step', 'updated_at'])

    xp_gained = 0
    if len(subchapters) > 0:
        last_idx = len(subchapters) - 1
        last_chunks = subchapters[last_idx].get('chunks') or [0]
        advanced_ok = subchapter_idx <= prev_step + 1
        at_end = subchapter_idx >= last_idx and chunk_idx >= max(0, len(last_chunks) - 1)
        if advanced_ok and at_end and (last_idx == 0 or prev_step >= max(0, last_idx - 1)):
            from core.xp import grant_xp
            from core import xp_config as _xp_c
            course_key = _hybrid_course_key(session.chapter_subject, session.chapter_num)
            res = grant_xp(
                request.user, _xp_c.XP_COURSE_CHAPTER, _xp_c.SOURCE_COURSE,
                f'course:{request.user.pk}:{course_key}',
                extra={'subject': session.chapter_subject, 'num': session.chapter_num},
                daily_cap=_xp_c.DAILY_CAP_COURSE,
            )
            if res.granted:
                xp_gained = res.amount

    return JsonResponse({
        'ok': True,
        'subchapter_idx': subchapter_idx,
        'chunk_idx': chunk_idx,
        'xp_gained': xp_gained,
    })


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
            chapter_context = pdf_loader.get_note_chapter_ai_context(
                session.chapter_subject, session.chapter_num, max_chars=7000,
            )
            if chapter_context:
                chapter_context = _extract_relevant_note_section(
                    chapter_context, section_title, max_chars=7000,
                )
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
        for msg in history[-4:]:
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
def api_save_school(request):
    """AJAX — Sauvegarde l'école choisie par l'utilisateur."""
    try:
        data = json.loads(request.body or '{}')
        school_name = str(data.get('school', '')).strip()
        if not school_name:
            return JsonResponse({'ok': False, 'error': 'Le nom de l\'école est requis.'}, status=400)
        
        from accounts.models import UserProfile, School
        profile = request.user.profile
        profile.school = school_name
        profile.save(update_fields=['school'])
        
        # Enregistrer l'école dans la base globale pour les suggestions
        School.objects.get_or_create(name=school_name)
        
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def api_set_coach_name(request):
    """AJAX — nom personnel de l'assistant IA."""
    try:
        data = json.loads(request.body or '{}')
        name = str(data.get('coach_name', '')).strip()
        if not name:
            return JsonResponse({'ok': False, 'error': 'Choisis un nom pour ton IA.'}, status=400)
        if len(name) > 40:
            name = name[:40]
        from accounts.models import UserProfile
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.coach_name = name
        profile.save(update_fields=['coach_name'])
        return JsonResponse({'ok': True, 'coach_name': name})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)


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
        base_qs = UserProfile.objects.exclude(user__id__in=exclude_ids).filter(
            user__is_staff=False,
            user__agent__isnull=True,
            user__is_active=True,
        ).select_related('user')

        # Priorité : même école
        if profile.school:
            s_school = list(base_qs.filter(school=profile.school)[:5])
            suggestions += s_school
            # Éviter les doublons pour les étapes suivantes
            for p in s_school: exclude_ids.add(p.user_id)

        # Ensuite : même série
        if len(suggestions) < 10 and profile.serie:
            s_serie = list(base_qs.exclude(user__id__in=exclude_ids).filter(serie=profile.serie)[:5])
            suggestions += s_serie
            for p in s_serie: exclude_ids.add(p.user_id)

        # Enfin : suggestions globales (les plus récents)
        if len(suggestions) < 10:
            s_global = list(base_qs.exclude(user__id__in=exclude_ids).order_by('-user__date_joined')[:10 - len(suggestions)])
            suggestions += s_global
    except Exception:
        pass


    # Get last message for each friend (for WhatsApp-style preview)
    from accounts.models import FriendMessage
    from core.premium import is_premium as _is_prem
    user_is_premium = _is_prem(request.user)

    # Bulk fetch unread counts
    from django.db.models import Count
    unread_counts = {
        item['sender']: item['count']
        for item in FriendMessage.objects.filter(receiver=request.user, is_read=False, is_system=False)
        .values('sender')
        .annotate(count=Count('id'))
    }

    # Bulk fetch last messages
    # We fetch the last message for each unique friend in the list
    friend_ids = [f['user'].id for f in friends]
    # For SQLite/Postgres compatibility, we fetch recent messages and pick the latest in Python
    # since 'distinct on' is Postgres-only and Subquery can be slow or complex.
    # Given the friend list is usually small (e.g. < 50), fetching the last 500 messages is safe.
    recent_msgs = FriendMessage.objects.filter(
        (Q(sender=request.user, receiver_id__in=friend_ids) |
         Q(receiver=request.user, sender_id__in=friend_ids))
    ).order_by('-created_at')[:150]

    last_msg_map = {}
    for msg in recent_msgs:
        other_id = msg.receiver_id if msg.sender_id == request.user.id else msg.sender_id
        if other_id not in last_msg_map:
            last_msg_map[other_id] = msg

    from accounts.names import alias_map_for
    aliases = alias_map_for(request.user)

    for f in friends:
        other = f['user']
        f['last_message'] = last_msg_map.get(other.id)
        f['unread_count'] = unread_counts.get(other.id, 0)
        f['display_name'] = aliases.get(other.id) or (other.get_full_name() or other.username)

    mention_candidates = [
        {
            'id': f['user'].id,
            'name': f['display_name'],
            'username': f['user'].username,
            'initial': (f['display_name'] or f['user'].username or 'U')[0].upper(),
        }
        for f in friends
    ]

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
        'mention_candidates': mention_candidates,
    })


@login_required
def api_friend_request(request):
    from accounts.models import Friendship
    from django.contrib.auth.models import User as DUser
    from django.db.models import Q

    if request.method == 'GET':
        # Search users or load suggestions
        q = request.GET.get('search', '').strip()
        
        relation_map = {}
        if len(q) >= 2:
            # Query-based search
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
            for rel in rel_qs:
                other_id = rel.to_user_id if rel.from_user_id == request.user.id else rel.from_user_id
                status = ''
                if rel.status == 'accepted':
                    status = 'friends'
                elif rel.status == 'pending':
                    status = 'pending_sent' if rel.from_user_id == request.user.id else 'pending_received'
                relation_map[other_id] = {'status': status, 'friendship_id': rel.id}
        else:
            # 7 suggestions: exclude current user and existing friends/invitations
            exclude_ids = set()
            exclude_ids.add(request.user.id)
            
            friend_rels = Friendship.objects.filter(
                Q(from_user=request.user) | Q(to_user=request.user)
            )
            for rel in friend_rels:
                exclude_ids.add(rel.from_user_id)
                exclude_ids.add(rel.to_user_id)
                
            user_school = ''
            try:
                user_school = request.user.profile.school or ''
            except Exception:
                pass
                
            same_school_users = []
            if user_school:
                same_school_users = list(DUser.objects.filter(
                    is_staff=False,
                    is_active=True,
                    agent__isnull=True,
                    profile__school=user_school
                ).exclude(id__in=exclude_ids).select_related('profile')[:7])
                
            needed = 7 - len(same_school_users)
            other_users = []
            if needed > 0:
                already_suggested = exclude_ids.copy()
                for su in same_school_users:
                    already_suggested.add(su.id)
                other_users = list(DUser.objects.filter(
                    is_staff=False,
                    is_active=True,
                    agent__isnull=True
                ).exclude(id__in=already_suggested).select_related('profile')[:needed])
                
            users = same_school_users + other_users

        from accounts.names import alias_map_for, display_name_for
        search_aliases = alias_map_for(request.user)
        result = []
        for u in users:
            try: p = u.profile
            except Exception: p = None
            rel = relation_map.get(u.id, {'status': '', 'friendship_id': None})
            result.append({
                'id': u.id,
                'username': u.username,
                'name': display_name_for(request.user, u, search_aliases),
                'school': p.school if p else '',
                'serie': p.serie if p else '',
                'photo_url': p.avatar.url if p and getattr(p, 'avatar', None) and p.avatar else None,
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
                    from core.push_events import push_friend_accepted
                    push_friend_accepted(request.user, existing.from_user if existing.from_user_id != request.user.id else existing.to_user)
                    return JsonResponse({'ok': True, 'created': False, 'status': 'accepted', 'auto_accepted': True})
                # declined -> on relance proprement dans le sens courant
                if existing.from_user_id != request.user.id or existing.to_user_id != to_user.id:
                    existing.from_user = request.user
                    existing.to_user = to_user
                existing.status = 'pending'
                existing.save(update_fields=['from_user', 'to_user', 'status', 'updated_at'])
                from core.push_events import push_friend_request
                push_friend_request(request.user, to_user)
                return JsonResponse({'ok': True, 'created': False, 'status': 'pending'})

            f = Friendship.objects.create(from_user=request.user, to_user=to_user, status='pending')
            from core.push_events import push_friend_request
            push_friend_request(request.user, to_user)
            return JsonResponse({'ok': True, 'created': True, 'status': f.status})
        except DUser.DoesNotExist:
            return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)

    elif action == 'accept':
        try:
            f = Friendship.objects.get(id=friendship_id, to_user=request.user)
            f.status = 'accepted'
            f.save()

            # Notification systeme pour les deux
            from accounts.models import FriendMessage
            FriendMessage.objects.create(
                sender=request.user, receiver=f.from_user,
                content=f"Vous êtes maintenant amis avec {request.user.get_full_name() or request.user.username} ! Vous pouvez commencer à discuter.",
                is_system=True
            )
            FriendMessage.objects.create(
                sender=f.from_user, receiver=request.user,
                content=f"Vous êtes maintenant amis avec {f.from_user.get_full_name() or f.from_user.username} ! Vous pouvez commencer à discuter.",
                is_system=True
            )
            from core.push_events import push_friend_accepted
            push_friend_accepted(request.user, f.from_user)
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
        from accounts.models import FriendAlias
        FriendAlias.objects.filter(
            Q(owner=request.user, friend_id=user_id) |
            Q(owner_id=user_id, friend=request.user)
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
    """GET: messages entre l'utilisateur et un ami.
    ?after_id=N → uniquement les messages plus récents + suppressions récentes.
    """
    from accounts.models import FriendMessage, Friendship
    from django.contrib.auth.models import User as DUser
    from django.db.models import Q
    from django.utils import timezone
    from datetime import timedelta

    try:
        friend = DUser.objects.get(pk=friend_id)
    except DUser.DoesNotExist:
        return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)

    is_friend = Friendship.objects.filter(
        Q(from_user=request.user, to_user=friend, status='accepted') |
        Q(from_user=friend, to_user=request.user, status='accepted')
    ).exists()
    if not is_friend:
        return JsonResponse({'error': 'Non ami'}, status=403)

    FriendMessage.objects.filter(
        sender=friend, receiver=request.user, is_read=False
    ).update(is_read=True)

    after_id = _parse_after_id(request)
    incremental = bool(after_id)
    pair = Q(sender=request.user, receiver=friend) | Q(sender=friend, receiver=request.user)
    if incremental:
        messages = list(FriendMessage.objects.filter(pair, id__gt=after_id).order_by('created_at')[:50])
    else:
        messages = list(reversed(list(FriendMessage.objects.filter(pair).order_by('-created_at')[:50])))

    ct = ContentType.objects.get_for_model(FriendMessage)
    msg_ids = [m.id for m in messages]
    deleted_user = set()
    deleted_all = set()
    if msg_ids:
        deleted_all = set(
            MessageDeletion.objects.filter(
                content_type=ct, object_id__in=msg_ids, for_all=True
            ).values_list('object_id', flat=True)
        )
        deleted_user = set(
            MessageDeletion.objects.filter(
                content_type=ct, object_id__in=msg_ids, for_all=False, deleted_by=request.user
            ).values_list('object_id', flat=True)
        )

    msgs = []
    for m in messages:
        if m.id in deleted_user:
            continue
        is_deleted = m.id in deleted_all
        msgs.append({
            'id': m.id,
            'sender_id': m.sender_id,
            'content': 'Message supprimé pour tous' if is_deleted else m.content,
            'created_at': _local_time(m.created_at).strftime('%H:%M'),
            'date': _local_time(m.created_at).strftime('%d/%m/%Y'),
            'is_mine': m.sender_id == request.user.id,
            'is_read': m.is_read,
            'is_system': m.is_system,
            'is_deleted': is_deleted,
        })

    deleted_for_me = []
    deleted_for_all = []
    if incremental:
        cutoff = timezone.now() - timedelta(days=21)
        del_qs = MessageDeletion.objects.filter(content_type=ct, deleted_at__gte=cutoff)
        deleted_for_all = list(
            del_qs.filter(for_all=True, object_id__lte=after_id).values_list('object_id', flat=True)[:300]
        )
        deleted_for_me = list(
            del_qs.filter(for_all=False, deleted_by=request.user, object_id__lte=after_id)
            .values_list('object_id', flat=True)[:300]
        )

    return JsonResponse({
        'ok': True,
        'messages': msgs,
        'incremental': incremental,
        'after_id': after_id,
        'deleted_for_me': deleted_for_me,
        'deleted_for_all': deleted_for_all,
    })


@login_required
@require_POST
def api_friend_send_message(request):
    """POST: envoie un message à un ami."""
    from accounts.models import FriendMessage, Friendship
    from django.contrib.auth.models import User as DUser

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
    from core.push_events import push_dm
    push_dm(request.user, friend, content)

    return JsonResponse({
        'ok': True,
        'message': {
            'id': msg.id,
            'content': msg.content,
            'created_at': _local_time(msg.created_at).strftime('%H:%M'),
            'date': _local_time(msg.created_at).strftime('%d/%m/%Y'),
            'is_mine': True,
        }
    })


@login_required
def api_friend_unread_count(request):
    """GET: badge Messages (contacts DM + tags groupe / point)."""
    from accounts.chat_groups import unread_badge_payload
    payload = unread_badge_payload(request.user)
    return JsonResponse(payload)


@login_required
@require_POST
def api_friend_alias(request):
    """POST: {friend_id, alias} — surnom privé. Alias vide = suppression."""
    from accounts.models import Friendship, FriendAlias
    from accounts.names import public_name
    from django.contrib.auth.models import User as DUser
    from django.db.models import Q
    from django.core.cache import cache

    data, err = _parse_json_body(request)
    if err:
        return err
    try:
        friend_id = int(data.get('friend_id') or 0)
    except (TypeError, ValueError):
        friend_id = 0
    alias = (data.get('alias') or '').strip()
    if not friend_id or friend_id == request.user.id:
        return JsonResponse({'ok': False, 'error': 'Ami introuvable'}, status=400)
    is_friend = Friendship.objects.filter(
        Q(from_user=request.user, to_user_id=friend_id, status='accepted') |
        Q(to_user=request.user, from_user_id=friend_id, status='accepted')
    ).exists()
    if not is_friend:
        return JsonResponse({'ok': False, 'error': "Tu ne peux donner un surnom qu'à un ami"}, status=400)
    try:
        friend = DUser.objects.get(id=friend_id, is_active=True)
    except DUser.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Ami introuvable'}, status=404)
    if len(alias) > 40:
        return JsonResponse({'ok': False, 'error': 'Surnom trop long (40 caractères max)'}, status=400)
    if not alias:
        FriendAlias.objects.filter(owner=request.user, friend_id=friend_id).delete()
        display = public_name(friend)
    else:
        FriendAlias.objects.update_or_create(
            owner=request.user,
            friend_id=friend_id,
            defaults={'alias': alias},
        )
        display = alias
    cache.delete(f'genius:hub:{request.user.pk}')
    return JsonResponse({
        'ok': True,
        'friend_id': friend_id,
        'alias': alias,
        'display_name': display,
        'real_name': public_name(friend),
    })


@login_required
@require_POST
def api_push_register(request):
    """POST: {token} — enregistre le jeton FCM du navigateur."""
    from accounts.models import PushDevice
    data, err = _parse_json_body(request)
    if err:
        return err
    token = (data.get('token') or '').strip()
    if not token or len(token) < 20:
        return JsonResponse({'ok': False, 'error': 'Jeton invalide'}, status=400)
    ua = (request.META.get('HTTP_USER_AGENT') or '')[:300]
    PushDevice.objects.update_or_create(
        token=token,
        defaults={'user': request.user, 'enabled': True, 'user_agent': ua},
    )
    return JsonResponse({'ok': True})


@login_required
@require_POST
def api_push_disable(request):
    from accounts.models import PushDevice
    data, err = _parse_json_body(request)
    if err:
        return err
    token = (data.get('token') or '').strip()
    if token:
        PushDevice.objects.filter(user=request.user, token=token).update(enabled=False)
    else:
        PushDevice.objects.filter(user=request.user).update(enabled=False)
    return JsonResponse({'ok': True})


def _request_group_id(request, data=None):
    raw = None
    if isinstance(data, dict):
        raw = data.get('group_id')
    if raw in (None, '', False):
        raw = request.GET.get('group_id') or request.POST.get('group_id')
    if raw in (None, '', False):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


_GROUP_QUIZ_TYPE_ALIASES = {
    'directe': 'word',
    'complete': 'word',
    'direct': 'word',
    'fill': 'word',
}


def _parse_after_id(request):
    try:
        return max(0, int(request.GET.get('after_id') or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_group_quiz_data(quiz_data):
    """Aligne un quiz de groupe sur le format Extra bèt (word/qcm/match/parts)."""
    if not isinstance(quiz_data, dict):
        return None, 'Quiz invalide.'
    qtype = str(quiz_data.get('question_type') or quiz_data.get('type') or 'word').strip().lower()
    qtype = _GROUP_QUIZ_TYPE_ALIASES.get(qtype, qtype)
    prompt = str(quiz_data.get('prompt') or quiz_data.get('question') or '').strip()
    answer_raw = quiz_data.get('answer', '')
    if isinstance(answer_raw, dict):
        answer = json.dumps(answer_raw, ensure_ascii=False)
    else:
        answer = str(answer_raw or '').strip()
    options = quiz_data.get('options')
    if qtype == 'qcm':
        options = options if isinstance(options, list) else (quiz_data.get('choices') or [])
        options = [str(o).strip() for o in options if str(o).strip()]
    elif qtype in ('match', 'parts'):
        options = options if isinstance(options, dict) else {}
    else:
        options = options if isinstance(options, list) else []

    from core.extra_bet_grader import ALLOWED_TYPES, verify_extra_bet_submission
    if qtype not in ALLOWED_TYPES:
        return None, 'Type de question invalide.'
    subject = str(quiz_data.get('subject') or '').strip().lower()
    if subject and subject not in MATS:
        return None, 'Matière invalide.'
    verdict = verify_extra_bet_submission(qtype, prompt, answer, options)
    if not verdict.get('valid', True):
        return None, verdict.get('reason') or 'La réponse proposée ne semble pas correcte.'
    return {
        'subject': subject,
        'question_type': qtype,
        'type': qtype,
        'question': prompt,
        'prompt': prompt,
        'answer': answer,
        'options': options,
        'choices': options if qtype == 'qcm' else [],
    }, None


def api_group_chat_history(request):
    """GET: historique live groupe — guest en lecture seule, compte en complet.
    ?after_id=N → uniquement les messages plus récents + suppressions récentes.
    ?group_id=N → un groupe précis (défaut: OU TOU BON).
    """
    after_id = _parse_after_id(request)
    from accounts.chat_groups import resolve_group
    group_id = _request_group_id(request)
    user = request.user if request.user.is_authenticated else None
    if user is None and not request.session.get('guest_mode'):
        return JsonResponse({'error': 'auth required'}, status=401)
    group = resolve_group(user, group_id, write=False)
    if group is None:
        return JsonResponse({'error': 'Groupe introuvable'}, status=404)
    return _group_chat_history_payload(user, after_id=after_id, group=group)


def api_group_chat_history_public(request):
    return api_group_chat_history(request)


def _invalidate_study_groups_cache(*users_or_ids):
    from django.core.cache import cache
    seen = set()
    for item in users_or_ids:
        if item is None:
            continue
        uid = getattr(item, 'id', item)
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        if uid in seen:
            continue
        seen.add(uid)
        cache.delete(f'study_groups_v1:{uid}')


@login_required
def api_study_groups(request):
    """GET: liste des groupes de l'utilisateur (OTB, Génies, custom)."""
    from django.core.cache import cache
    from accounts.chat_groups import list_groups_for_user
    from accounts.models import Friendship
    from django.db.models import Q
    ck = f'study_groups_v1:{request.user.id}'
    cached = cache.get(ck)
    if cached is not None:
        return JsonResponse(cached)
    groups = list_groups_for_user(request.user)
    friends = []
    rels = Friendship.objects.filter(
        Q(from_user=request.user, status='accepted') | Q(to_user=request.user, status='accepted')
    ).select_related('from_user', 'to_user')
    from accounts.names import alias_map_for, display_name_for
    aliases = alias_map_for(request.user)
    seen = set()
    for rel in rels:
        other = rel.to_user if rel.from_user_id == request.user.id else rel.from_user
        if other.id in seen:
            continue
        seen.add(other.id)
        friends.append({
            'id': other.id,
            'name': display_name_for(request.user, other, aliases),
            'username': other.username,
        })
    friends.sort(key=lambda f: (f['name'] or '').lower())
    payload = {'ok': True, 'groups': groups, 'friends': friends}
    cache.set(ck, payload, 45)
    return JsonResponse(payload)


@login_required
@require_POST
def api_study_group_create(request):
    from accounts.chat_groups import create_custom_group, serialize_group
    data, err = _parse_json_body(request)
    if err:
        return err
    group, error = create_custom_group(
        request.user,
        data.get('name'),
        data.get('friend_ids') or [],
        data.get('usernames') or [],
    )
    if error:
        return JsonResponse({'ok': False, 'error': error}, status=400)
    member_ids = list(group.memberships.values_list('user_id', flat=True))
    _invalidate_study_groups_cache(request.user.id, *member_ids)
    return JsonResponse({'ok': True, 'group': serialize_group(group, request.user)})


@login_required
@require_POST
def api_study_group_invite(request):
    from accounts.chat_groups import invite_to_group, resolve_group, serialize_group
    data, err = _parse_json_body(request)
    if err:
        return err
    group = resolve_group(request.user, data.get('group_id'), write=True)
    if group is None:
        return JsonResponse({'ok': False, 'error': 'Groupe introuvable'}, status=404)
    added, error = invite_to_group(
        request.user, group, data.get('friend_ids') or [], data.get('usernames') or []
    )
    if error:
        return JsonResponse({'ok': False, 'error': error}, status=400)
    member_ids = list(group.memberships.values_list('user_id', flat=True))
    added_ids = [u.id if hasattr(u, 'id') else u for u in (added if isinstance(added, list) else [])]
    _invalidate_study_groups_cache(request.user.id, *member_ids, *added_ids)
    return JsonResponse({'ok': True, 'added': added, 'group': serialize_group(group, request.user)})


def _group_chat_history_payload(user, after_id=0, group=None):
    from accounts.models import GroupMessage, Friendship, GroupMessageNotification, GroupChatQuizAttempt
    from accounts.chat_groups import group_messages_qs, mark_group_read, get_or_create_official_group
    from django.db.models import Q, Count
    from django.utils import timezone
    from datetime import timedelta

    if group is None:
        group = get_or_create_official_group()

    incremental = bool(after_id)
    qs = group_messages_qs(group).select_related(
        'sender__profile',
        'reply_to__sender__profile',
    ).prefetch_related('mentions').annotate(
        quiz_attempts_count=Count('quiz_attempts', distinct=True),
        quiz_correct_count=Count('quiz_attempts', filter=Q(quiz_attempts__is_correct=True), distinct=True),
    )
    if incremental:
        messages = list(qs.filter(id__gt=after_id).order_by('created_at')[:80])
    else:
        messages = list(reversed(list(qs.order_by('-created_at')[:80])))
    uid = getattr(user, 'id', None) if user and getattr(user, 'is_authenticated', False) else None
    from accounts.names import alias_map_for, display_name_for
    aliases = alias_map_for(user) if uid else {}
    sender_ids = {msg.sender_id for msg in messages if uid and msg.sender_id != uid}
    friend_map = {}
    if uid and sender_ids:
        rels = Friendship.objects.filter(
            Q(from_user_id=uid, to_user_id__in=sender_ids, status='accepted') |
            Q(to_user_id=uid, from_user_id__in=sender_ids, status='accepted')
        )
        for rel in rels:
            other_id = rel.to_user_id if rel.from_user_id == uid else rel.from_user_id
            friend_map[other_id] = rel.id
    my_attempts = {}
    if uid and messages:
        for attempt in GroupChatQuizAttempt.objects.filter(
            user_id=uid, message_id__in=[m.id for m in messages if m.quiz_data]
        ):
            my_attempts[attempt.message_id] = {
                'submitted': attempt.submitted_answer,
                'is_correct': attempt.is_correct,
            }
    result = []
    for msg in messages:
        sender = msg.sender
        profile = getattr(sender, 'profile', None)
        attempts_count = msg.quiz_attempts_count if msg.quiz_data else 0
        correct_count = msg.quiz_correct_count if msg.quiz_data else 0

        sender_shown = display_name_for(user, sender, aliases)
        result.append({
            'id': msg.id,
            'sender_id': sender.id,
            'sender_name': sender_shown,
            'sender_school': profile.school if profile else '',
            'sender_initial': (sender_shown or sender.username or 'U')[0].upper(),
            'sender_avatar_url': profile.avatar.url if profile and getattr(profile, 'avatar', None) and profile.avatar else None,
            'sender_is_admin': sender.is_staff or sender.is_superuser,
            'sender_is_you': bool(uid and sender.id == uid),
            'sender_is_friend': sender.id in friend_map,
            'sender_friendship_id': friend_map.get(sender.id),
            'content': msg.content,
            'quiz_data': msg.quiz_data,
            'reply_to': ({
                'id': msg.reply_to_id,
                'sender_id': msg.reply_to.sender_id,
                'sender_name': display_name_for(user, msg.reply_to.sender, aliases),
                'content': (msg.reply_to.content or ('Quiz' if msg.reply_to.quiz_data else 'Média'))[:160],
            } if msg.reply_to_id and msg.reply_to else None),
            'mentions': [
                {'id': u.id, 'name': display_name_for(user, u, aliases), 'username': u.username}
                for u in msg.mentions.all()
            ],
            'mention_everyone': bool(getattr(msg, 'mention_everyone', False)),
            'quiz_attempt_count': attempts_count,
            'quiz_correct_count': correct_count,
            'quiz_stats': {
                'attempts': attempts_count,
                'pct': round(100 * correct_count / attempts_count) if attempts_count else 0,
            } if msg.quiz_data else None,
            'my_attempt': my_attempts.get(msg.id),
            'image_url': msg.image.url if getattr(msg, 'image', None) and msg.image else None,
            'video_url': msg.video.url if getattr(msg, 'video', None) and msg.video else None,
            'created_at': _local_time(msg.created_at).strftime('%H:%M'),
            'date': _local_time(msg.created_at).strftime('%d/%m/%Y'),
        })

    from django.contrib.contenttypes.models import ContentType
    from accounts.models import MessageDeletion
    ct = ContentType.objects.get_for_model(GroupMessage)
    msg_ids = [m['id'] for m in result]
    deleted_user = set()
    deleted_all = set()
    if msg_ids:
        deleted_all = set(
            MessageDeletion.objects.filter(
                content_type=ct, object_id__in=msg_ids, for_all=True
            ).values_list('object_id', flat=True)
        )
        if uid:
            deleted_user = set(
                MessageDeletion.objects.filter(
                    content_type=ct, object_id__in=msg_ids, for_all=False, deleted_by_id=uid
                ).values_list('object_id', flat=True)
            )
    final = []
    for m in result:
        mid = m['id']
        if mid in deleted_user:
            continue
        if mid in deleted_all:
            m['content'] = 'Message supprimé pour tous'
            m['is_deleted'] = True
            m['image_url'] = None
            m['video_url'] = None
        else:
            m['is_deleted'] = False
        final.append(m)

    if uid:
        visible_ids = [m['id'] for m in final]
        max_visible = max(visible_ids) if visible_ids else after_id
        mark_group_read(user, group, max_visible)

    deleted_for_me = []
    deleted_for_all = []
    if incremental:
        cutoff = timezone.now() - timedelta(days=21)
        del_qs = MessageDeletion.objects.filter(content_type=ct, deleted_at__gte=cutoff)
        deleted_for_all = list(
            del_qs.filter(for_all=True, object_id__lte=after_id).values_list('object_id', flat=True)[:300]
        )
        if uid:
            deleted_for_me = list(
                del_qs.filter(for_all=False, deleted_by_id=uid, object_id__lte=after_id)
                .values_list('object_id', flat=True)[:300]
            )

    return JsonResponse({
        'ok': True,
        'messages': final,
        'readonly': uid is None,
        'incremental': incremental,
        'after_id': after_id,
        'deleted_for_me': deleted_for_me,
        'deleted_for_all': deleted_for_all,
        'group_id': group.id if group else None,
        'group_kind': group.kind if group else 'official',
    })


@login_required
@require_POST
def api_group_chat_send(request):
    """POST: envoie un message ou un média dans le chat de groupe."""
    from accounts.models import GroupMessage, GroupMessageNotification, StudyChatGroupMember
    from accounts.chat_groups import resolve_group, group_messages_qs
    from django.contrib.auth.models import User as DUser

    content = ''
    media = None
    media_type = 'image'
    quiz_data = None
    reply_to_id = None
    mention_ids = []
    mention_everyone = False
    is_admin_mode = False
    group_id = None

    if request.content_type and request.content_type.startswith('multipart/form-data'):
        content = request.POST.get('content', '').strip()
        media = request.FILES.get('media')
        media_type = request.POST.get('media_type', 'image')
        reply_to_id = request.POST.get('reply_to_id')
        mention_ids = request.POST.getlist('mention_ids')
        mention_everyone = str(request.POST.get('mention_everyone', '')).lower() in ('1', 'true', 'yes')
        is_admin_mode = str(request.POST.get('is_admin_mode', '')).lower() in ('1', 'true', 'yes')
        group_id = _request_group_id(request)
    else:
        data, _err = _parse_json_body(request)
        if _err:
            return _err
        content = data.get('content', '').strip()
        media_type = data.get('media_type', 'image')
        quiz_data = data.get('quiz_data')
        reply_to_id = data.get('reply_to_id')
        mention_ids = data.get('mention_ids') or []
        mention_everyone = bool(data.get('mention_everyone', False))
        is_admin_mode = bool(data.get('is_admin_mode', False))
        group_id = _request_group_id(request, data)

    group = resolve_group(request.user, group_id, write=True)
    if group is None:
        return JsonResponse({'error': 'Groupe introuvable'}, status=404)

    sender = request.user
    if is_admin_mode and request.session.get('_otb_admin_ok') is True:
        admin_user = DUser.objects.filter(is_superuser=True).first() or DUser.objects.filter(is_staff=True).first()
        if admin_user:
            sender = admin_user

    if not content and not media and not quiz_data:
        return JsonResponse({'error': 'Message, média ou quiz requis.'}, status=400)

    # Quiz groupe : même validation Extra bèt (règles locales, 0 IA)
    if quiz_data:
        quiz_data, quiz_err = _normalize_group_quiz_data(quiz_data)
        if quiz_err:
            return JsonResponse({'ok': False, 'error': quiz_err}, status=200)

    if media and media_type == 'video' and not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Seul l\'équipe peut envoyer des vidéos.'}, status=403)

    reply_to = None
    if reply_to_id:
        try:
            reply_to = group_messages_qs(group).select_related('sender').get(pk=int(reply_to_id))
        except (TypeError, ValueError, GroupMessage.DoesNotExist):
            reply_to = None

    msg_kwargs = {'sender': sender, 'content': content, 'mention_everyone': mention_everyone, 'group': group}
    if reply_to:
        msg_kwargs['reply_to'] = reply_to
    if quiz_data:
        msg_kwargs['quiz_data'] = quiz_data
        
    if media:
        if media_type == 'video':
            msg_kwargs['video'] = media
        else:
            msg_kwargs['image'] = media

    msg = GroupMessage.objects.create(**msg_kwargs)
    clean_mention_ids = []
    if isinstance(mention_ids, list):
        for raw_id in mention_ids:
            try:
                clean_mention_ids.append(int(raw_id))
            except (TypeError, ValueError):
                pass
    mention_users = list(DUser.objects.filter(id__in=set(clean_mention_ids), is_active=True)) if clean_mention_ids else []
    if mention_users:
        msg.mentions.set(mention_users)

    notification_targets = {}
    for user in mention_users:
        if user.id != sender.id:
            notification_targets[(user.id, 'mention')] = user
    if mention_everyone:
        if group.kind == 'official':
            everyone_qs = DUser.objects.filter(is_active=True, is_staff=False).exclude(id=sender.id)[:500]
        else:
            member_ids = StudyChatGroupMember.objects.filter(group=group).exclude(user_id=sender.id).values_list('user_id', flat=True)
            everyone_qs = DUser.objects.filter(id__in=member_ids, is_active=True)
        for user in everyone_qs:
            notification_targets[(user.id, 'everyone')] = user
    if reply_to and reply_to.sender_id != sender.id:
        notification_targets[(reply_to.sender_id, 'reply')] = reply_to.sender
    for (_uid, reason), user in notification_targets.items():
        GroupMessageNotification.objects.get_or_create(message=msg, recipient=user, reason=reason)
    if notification_targets:
        from core.push_events import push_group_mention
        by_reason = {}
        for (_uid, reason), user in notification_targets.items():
            by_reason.setdefault(reason, []).append(user)
        preview = content or ('Quiz' if quiz_data else 'Média')
        for reason, users in by_reason.items():
            push_group_mention(sender, users, group, reason, preview)

    from accounts.names import alias_map_for, display_name_for
    aliases = alias_map_for(sender)
    sender_shown = display_name_for(sender, sender, aliases)
    return JsonResponse({
        'ok': True,
        'message': {
            'id': msg.id,
            'sender_id': sender.id,
            'sender_name': sender_shown,
            'sender_school': getattr(getattr(sender, 'profile', None), 'school', ''),
            'sender_initial': (sender_shown or sender.username or 'U')[0].upper(),
            'sender_avatar_url': sender.profile.avatar.url if getattr(sender, 'profile', None) and sender.profile.avatar else None,
            'sender_is_admin': sender.is_staff or sender.is_superuser,
            'sender_is_you': True,
            'content': msg.content,
            'quiz_data': msg.quiz_data,
            'reply_to': ({
                'id': reply_to.id,
                'sender_id': reply_to.sender_id,
                'sender_name': display_name_for(sender, reply_to.sender, aliases),
                'content': (reply_to.content or ('Quiz' if reply_to.quiz_data else 'Média'))[:160],
            } if reply_to else None),
            'mentions': [
                {'id': u.id, 'name': display_name_for(sender, u, aliases), 'username': u.username}
                for u in mention_users
            ],
            'mention_everyone': msg.mention_everyone,
            'image_url': msg.image.url if msg.image else None,
            'video_url': msg.video.url if msg.video else None,
            'created_at': _local_time(msg.created_at).strftime('%H:%M'),
            'date': _local_time(msg.created_at).strftime('%d/%m/%Y'),
            'group_id': group.id,
        }
    })


@login_required
@require_POST
def api_friend_delete_message(request):
    """POST: {message_id: int, for_all: bool} Delete a friend message for me or for all.
    If for_all is true, only the sender or staff can delete for all.
    """
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    message_id = data.get('message_id')
    for_all = bool(data.get('for_all', False))
    from accounts.models import FriendMessage
    try:
        msg = FriendMessage.objects.get(pk=message_id)
    except FriendMessage.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    # Permission checks
    if for_all and not (msg.sender_id == request.user.id or request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Permission refusee'}, status=403)

    ct = ContentType.objects.get_for_model(FriendMessage)
    # create deletion record
    MessageDeletion.objects.create(content_type=ct, object_id=msg.id, deleted_by=request.user, for_all=for_all)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def api_group_delete_message(request):
    """POST: {message_id: int, for_all: bool} Delete a group message for me or for all.
    If for_all is true, only the sender or staff can delete for all.
    """
    data, _err = _parse_json_body(request)
    if _err:
        return _err
    message_id = data.get('message_id')
    for_all = bool(data.get('for_all', False))
    from accounts.models import GroupMessage
    try:
        msg = GroupMessage.objects.get(pk=message_id)
    except GroupMessage.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    if for_all and not (msg.sender_id == request.user.id or request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Permission refusee'}, status=403)

    ct = ContentType.objects.get_for_model(GroupMessage)
    MessageDeletion.objects.create(content_type=ct, object_id=msg.id, deleted_by=request.user, for_all=for_all)
    return JsonResponse({'ok': True})


@login_required
def api_admin_message(request):
    """GET: fetch latest OUTOUBON message for user. POST (superuser): send motivational message."""
    from accounts.models import AdminMessage

    if request.method == 'POST':
        data, _err = _parse_json_body(request)
        if _err:
            return _err
        if data.get('mark_read'):
            AdminMessage.objects.filter(receiver=request.user, is_read=False).update(is_read=True)
            return JsonResponse({'ok': True})
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

    # GET: return latest admin message without marking as read
    msg = AdminMessage.objects.filter(receiver=request.user).order_by('-created_at').first()
    if msg:
        return JsonResponse({
            'ok': True,
            'message': {
                'id': msg.id,
                'content': msg.content,
                'created_at': msg.created_at.strftime('%H:%M'),
                'date': msg.created_at.strftime('%d/%m/%Y'),
                'is_read': bool(msg.is_read),
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


@login_required
def api_user_foreign_lang(request):
    """GET: retourne la langue étrangère choisie. POST: met à jour."""
    import json as _json
    if request.method == 'POST':
        try:
            body = _json.loads(request.body)
            selected = body.get('foreign_lang', 'anglais')
            if selected not in ('anglais', 'espagnol'):
                selected = 'anglais'
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            profile.langue_etrangere = selected
            profile.save(update_fields=['langue_etrangere'])
            return JsonResponse({'ok': True, 'foreign_lang': selected})
        except Exception:
            _logger.exception('Server error')
            return JsonResponse({'error': 'Erreur interne du serveur.'}, status=400)
    try:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        selected = profile.langue_etrangere or 'anglais'
    except Exception:
        selected = 'anglais'
    return JsonResponse({'foreign_lang': selected})


@require_POST
def api_exercise_chat(request):
    """
    Chat IA guidé pour un exercice (moteur type Astra).
    POST: {exercise, subject, messages, message, student_name, session_state?, image_b64?, image_mime?}
    Retourne: {ok, response, session, session_state, meta}
    """
    if not request.user.is_authenticated and not _is_guest(request):
        return JsonResponse({'error': 'login_required'}, status=401)

    if _is_guest(request):
        guest_exo_chat = int(request.session.get('guest_exo_chat_count', 0) or 0)
        if guest_exo_chat >= 5:
            return JsonResponse({'error': 'guest_limit', 'signup_url': '/signup/'}, status=403)
        request.session['guest_exo_chat_count'] = guest_exo_chat + 1
        request.session.modified = True

    try:
        from . import exercise_tutor as et

        data = json.loads(request.body)
        exercise = data.get('exercise', {})
        subject = data.get('subject', 'maths')

        # Réhydrater la solution note_ai (non envoyée au client)
        if exercise.get('_from_note_ai') and exercise.get('_note_ai_id'):
            try:
                from django.core.cache import cache as _dj_cache
                _cache_uid = (
                    request.user.id
                    if request.user.is_authenticated
                    else request.session.session_key or 'guest'
                )
                _cached_sol = _dj_cache.get(
                    f'exo_sol:{_cache_uid}:{exercise.get("_note_ai_id")}'
                )
                if _cached_sol:
                    exercise = dict(exercise)
                    exercise['solution'] = _cached_sol
            except Exception:
                pass

        messages = data.get('messages', [])
        user_message = data.get('message', '').strip()
        student_name = data.get('student_name', "l'élève")
        session = data.get('session_state') or et.init_session(exercise)

        _ex_img_b64 = data.get('image_b64', '')
        _ex_img_mime = data.get('image_mime', 'image/jpeg')
        _ex_image_data = None
        _ex_image_mime = None
        if _ex_img_b64:
            try:
                import base64 as _b64mod
                _ex_image_data = _b64mod.b64decode(_ex_img_b64)
                _ex_image_mime = _ex_img_mime or 'image/jpeg'
                from core.ai_guard import prepare_image_bytes as _prep_img
                _ex_image_data, _ex_image_mime = _prep_img(_ex_image_data, _ex_image_mime)
            except Exception:
                pass

        if not user_message:
            return JsonResponse({'error': 'Message vide'}, status=400)

        _LANG_MAP = {
            'anglais': 'English',
            'espagnol': 'Spanish',
            'kreyol': 'Haitian Creole (Kreyòl Ayisyen)',
        }
        lang_rule = (
            f"CRITICAL LANGUAGE RULE: You MUST reply ENTIRELY in {_LANG_MAP[subject.lower()]}. "
            f"Do NOT mix languages.\n"
        ) if subject.lower() in _LANG_MAP else (
            "RÈGLE DE LANGUE : Tu réponds toujours en français.\n"
        )

        mode = et.detect_message_mode(user_message)

        if mode == 'intro':
            user_message = user_message.replace('[INTRO]', '').strip() or (
                "Présente brièvement cet exercice et aide l'élève à commencer, "
                "sans donner la solution complète."
            )

        if mode == 'answer' and not session.get('_quota_counted'):
            if _is_guest(request):
                _g_done = int(request.session.get('guest_exo_done', 0) or 0)
                request.session['guest_exo_done'] = _g_done + 1
                request.session.modified = True
            elif request.user.is_authenticated:
                from core.premium import increment_exercise
                increment_exercise(request.user, subject)
            session['_quota_counted'] = True

        if mode == 'hint':
            session = et.apply_hint(session)
            user_message = user_message.replace('[HINT]', '').strip() or "Donne-moi un indice pour la question en cours."
        elif mode == 'skip':
            user_message = "Je veux passer à la question suivante."
            session = et.apply_hint(session)
        elif mode == 'method':
            user_message = "Explique-moi la méthode pour cette question."
        elif mode == 'finish':
            user_message = 'Donne le bilan objectif et la note finale de ma session.'

        # ── Philosophie : prompts spécialisés conservés ───────────────────
        _philo_type = exercise.get('_philo_type', '')
        tutor_mode = data.get('mode') == 'tutor'
        if tutor_mode:
            system_prompt = et.build_tutor_prompt_short(
                exercise, subject, student_name, session, lang_rule,
            )
        elif subject.lower() == 'philosophie' and _philo_type:
            intro = exercise.get('intro') or exercise.get('enonce', '')
            questions = exercise.get('questions', [])
            texte_philo = exercise.get('texte', '')
            exercise_ctx = f"Énoncé: {intro[:1000]}"
            if texte_philo:
                exercise_ctx += f"\n\nTexte philosophique:\n{texte_philo}"
            if questions:
                exercise_ctx += "\n\nQuestions:\n" + "\n".join(f"  {i+1}. {q}" for i, q in enumerate(questions))
            _solution = exercise.get('solution', '')
            if _philo_type == 'dissertation':
                system_prompt = (
                    f"Tu es Prof Bac — prof de philosophie BAC Haïti pour {student_name}.\n"
                    f"RÈGLE DE LANGUE : français.\n\n"
                    f"Guide la dissertation étape par étape (intro → thèse → antithèse → conclusion).\n"
                    f"Ne donne jamais la réponse complète. Pose des questions socratiques.\n"
                    f"Termine par [NOTE:X/10] quand les 4 parties sont traitées.\n\n"
                    f"--- EXERCICE ---\n{exercise_ctx}\n--- FIN ---\n"
                )
            else:
                _phrase_q3 = exercise.get('_phrase_a_expliquer', '')
                system_prompt = (
                    f"Tu es Prof Bac — philosophie BAC Haïti pour {student_name}.\n"
                    f"Travaille Q1→Q4 en ordre strict. Une question à la fois.\n"
                    f"Phrase Q3 : \"{_phrase_q3}\"\n"
                    f"Termine par [NOTE:X/10] quand les 4 questions sont traitées.\n\n"
                    f"--- EXERCICE ---\n{exercise_ctx}\n--- FIN ---\n"
                )
        else:
            _exo_profile = ''
            _exo_level = 'intermediaire'
            _exo_score = 50
            if request.user.is_authenticated:
                try:
                    from .learning_tracker import get_adaptive_level, get_subject_level_score
                    from . import gemini as _gprof
                    _exo_level = get_adaptive_level(request.user, subject)
                    _exo_score = get_subject_level_score(request.user, subject)
                    _exo_profile = _gprof.build_user_learning_profile_short(request.user, subject=subject)
                except Exception:
                    pass
            system_prompt = et.build_system_prompt(
                exercise, subject, student_name, session, mode, lang_rule,
                student_profile=_exo_profile,
                mastery_level=_exo_level,
                mastery_score=_exo_score,
            )

        from . import gemini as _gemini
        hist_summary, recent_msgs = _gemini._build_compact_history(
            messages, keep=_gemini.HISTORY_SLIDING_WINDOW,
        )
        ai_messages = [{"role": "system", "content": system_prompt}]
        if hist_summary:
            ai_messages.append({"role": "system", "content": hist_summary})
        for msg in recent_msgs:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            if role in ('user', 'assistant') and content:
                ai_messages.append({"role": role, "content": content[:500]})
        if _ex_image_data:
            import base64 as _b64mod2
            _b64_ex = _b64mod2.b64encode(_ex_image_data).decode('utf-8')
            ai_messages.append({"role": "user", "content": [
                {"type": "text", "text": user_message},
                {"type": "image_url", "image_url": {"url": f"data:{_ex_image_mime};base64,{_b64_ex}"}},
            ]})
        else:
            ai_messages.append({"role": "user", "content": user_message})

        if not getattr(settings, 'DEEPSEEK_API_KEY', ''):
            return JsonResponse({
                'error': 'ia_unavailable',
                'message': 'Problème réseau.',
                'ok': False,
            }, status=503)

        _ex_model = _gemini.VISION_MODEL if _ex_image_data else _gemini.FAST_MODEL
        _max_tok = 180 if tutor_mode else (400 if mode == 'answer' else 320)
        resp = _gemini._tracked_create(
            model=_ex_model,
            messages=ai_messages,
            max_tokens=_max_tok,
        )
        raw = resp.choices[0].message.content or ''
        response, session, meta = et.parse_ai_directives(raw, session)

        if mode == 'skip' and not meta.get('advance') and not session.get('completed'):
            session = et.advance_question(session, 'skipped')

        idx = int(session.get('current_index') or 0)
        attempts = list(session.get('attempts') or [0] * session.get('total', 1))
        while len(attempts) < session.get('total', 1):
            attempts.append(0)
        if mode == 'answer' and idx < len(attempts):
            attempts[idx] = int(attempts[idx] or 0) + 1
            session['attempts'] = attempts

        import re as _re
        response = _re.sub(r'\*\*(.+?)\*\*', r'\1', response)
        response = _re.sub(r'\*(.+?)\*', r'\1', response)

        public_session = et.session_public_view(session, exercise)
        summary = et.compute_session_summary(session, exercise) if session.get('completed') else None
        return JsonResponse({
            'ok': True,
            'response': response.strip(),
            'session_state': session,
            'session': public_session,
            'meta': meta,
            'offer_tutor': et.should_offer_tutor(session),
            'summary': summary,
        })
    except Exception as exc:
        import traceback
        traceback.print_exc()
        from core.ai_usage import AiBudgetExceeded, budget_exceeded_json
        try:
            from openai import AuthenticationError, APIConnectionError, APIStatusError
        except ImportError:
            AuthenticationError = APIConnectionError = APIStatusError = tuple()
        if isinstance(exc, AiBudgetExceeded):
            return JsonResponse(budget_exceeded_json(exc.reason), status=429)
        if isinstance(exc, AuthenticationError):
            return JsonResponse({
                'error': 'ia_unavailable',
                'message': 'Problème réseau.',
                'ok': False,
            }, status=503)
        if isinstance(exc, (APIConnectionError, APIStatusError)):
            return JsonResponse({
                'error': 'ia_unavailable',
                'message': 'Problème réseau.',
            }, status=503)
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
    POST: {score, subject, activity_token}
    """
    try:
        data = json.loads(request.body)
        score = float(data.get('score', 0))
        score = max(0.0, min(10.0, score))  # clamp 0-10
        token = data.get('activity_token')
        fp = hashlib.sha256(
            f"{request.user.pk}|{data.get('subject')}|complete|{token}".encode()
        ).hexdigest()[:32]
        from core.xp import reward_exercise, get_user_xp
        xp_res = reward_exercise(
            request.user, fp, token=token, orphan=False, score_pct=score,
        )
        if xp_res.reason not in ('no_token', 'invalid_activity', 'too_fast', 'no_fingerprint'):
            stats = _get_or_create_stats(request.user)
            stats.exercices_resolus += 1
            minutes = max(5, round(score * 2))
            stats.minutes_etude += minutes
            stats.save(update_fields=['exercices_resolus', 'minutes_etude'])

        _update_streak(request.user)

        subject = (data.get('subject') or 'maths').strip()
        try:
            from .learning_tracker import update_subject_mastery, invalidate_ai_caches, log_learning_event
            update_subject_mastery(
                user=request.user,
                subject=subject,
                score_pct=score * 10,
                question_text='Session exercice guidée',
                topic='exercice_tutoré',
            )
            log_learning_event(
                user=request.user,
                event_type='exercise_corrected',
                subject=subject,
                details={'score_note': score, 'source': 'exercise_chat'},
                score_pct=score * 10,
            )
            invalidate_ai_caches(request.user)
        except Exception:
            pass

        new_xp = get_user_xp(request.user)
        stats = _get_or_create_stats(request.user)
        return JsonResponse({
            'ok': True,
            'xp': new_xp,
            'xp_gained': xp_res.amount if xp_res.granted else 0,
            'xp_reason': xp_res.reason,
            'exercices_resolus': stats.exercices_resolus,
        })
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

    # Matière active (filtre) — une seule section affichée
    all_sections = list(library)
    user_subjs = set()
    if request.user.is_authenticated:
        user_subjs = _get_user_serie_subjects(request.user)
    active_subject = (request.GET.get('subject') or '').strip().lower()
    available = [s for s in all_sections if not user_subjs or s['subject'] in user_subjs]
    if not available:
        available = all_sections
    if active_subject and any(s['subject'] == active_subject for s in available):
        library_display = [s for s in available if s['subject'] == active_subject]
    else:
        active_subject = available[0]['subject'] if available else ''
        library_display = available[:1] if available else []

    user_is_premium = False
    if request.user.is_authenticated:
        from core.premium import is_premium as _is_prem
        user_is_premium = _is_prem(request.user)

    return render(request, 'core/library.html', {
        'library': library_display,
        'library_all': available,
        'mats': MATS,
        'active_subject': active_subject,
        'user_serie_subjects': user_subjs,
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
    response = FileResponse(open(fpath, 'rb'), content_type='application/pdf')
    filename = os.path.basename(fpath)
    
    if request.GET.get('dl') == '1':
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
    else:
        # 'inline' allows viewing in browser if supported
        response['Content-Disposition'] = f'inline; filename="{filename}"'
    
    response['X-Content-Type-Options'] = 'nosniff'
    return response



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
            _krd_qs = list(_krd_qs)
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
        'maths':        'quiz_math.json',
    }
    if subject in _DUEL_JSON_FILES:
        from pathlib import Path as _djPath
        import json as _djj
        _dj_file = _djPath(__file__).resolve().parent.parent / 'database' / _DUEL_JSON_FILES[subject]
        try:
            _dj_data = _djj.loads(_dj_file.read_text(encoding='utf-8'))
            _dj_qs   = _dj_data if isinstance(_dj_data, list) else _dj_data.get('quiz', [])
            _dj_qs = list(_dj_qs)
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

    # ── SPECIAL: Anglais & Espagnol — JSON NS4, sinon IA ──
    if subject in ('anglais', 'espagnol'):
        from pathlib import Path as _langPath
        import json as _langj
        _lang_file = _langPath(__file__).resolve().parent.parent / 'database' / f'quiz_{subject}.json'
        try:
            _lang_raw = _langj.loads(_lang_file.read_text(encoding='utf-8'))
            _lang_qs = _lang_raw if isinstance(_lang_raw, list) else _lang_raw.get('quiz', [])
            random.shuffle(_lang_qs)
            for _q in _lang_qs:
                _opts = list(_q.get('options') or [])
                if len(_opts) < 4:
                    continue
                _cidx = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(str(_q.get('correct', 'A')).upper(), 0)
                _ans = _opts[_cidx] if _cidx < len(_opts) else ''
                random.shuffle(_opts)
                try:
                    _rc = _opts.index(_ans)
                except ValueError:
                    _rc = 0
                questions.append({
                    'enonce':           _q.get('question', ''),
                    'options':          _opts[:4],
                    'reponse_correcte': _rc,
                    'explication':      _q.get('explanation', ''),
                    'theme':            _q.get('category', subject),
                    'difficulte':       _q.get('difficulty', 'moyen'),
                    'source':           f'quiz_{subject}_json',
                })
        except Exception:
            import traceback
            traceback.print_exc()
        if len(questions) >= count:
            random.shuffle(questions)
            return questions[:count]
        ai_qs = gemini.generate_quiz_questions(subject, count=count - len(questions))
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

    if subject != 'aleatoire' and subject not in MATS:
        return JsonResponse({'error': 'Matière invalide'}, status=400)

    expires = _tz.now() + timedelta(minutes=15)
    code    = QuizDuel.generate_code()

    if subject == 'aleatoire':
        from core.matchmaking import _fetch_mixed_questions
        questions = _fetch_mixed_questions(request.user, count=count)
        store_subject = 'aleatoire'
        is_mixed = True
    else:
        questions = _fetch_duel_questions(subject, count=count)
        store_subject = subject
        is_mixed = False

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
        subject    = store_subject,
        questions  = questions,
        expires_at = expires,
        status     = 'waiting',
        match_mode = 'private',
        is_mixed_subjects = is_mixed,
    )
    return JsonResponse({
        'code':       duel.code,
        'subject':    store_subject,
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

    if duel.is_ghost_opponent:
        return JsonResponse({'error': 'Ce match est un Génie Fantôme — pas de jointure.'}, status=400)

    if duel.status != 'waiting':
        return JsonResponse({'error': 'Ce duel est déjà en cours ou terminé.'}, status=409)

    if duel.creator == request.user:
        return JsonResponse({'error': 'Tu ne peux pas défier toi-même !'}, status=400)

    duel.challenger = request.user
    duel.is_live_race = True
    duel.save(update_fields=['challenger', 'is_live_race'])

    from core.live_duel import start_live_duel
    from accounts.names import display_name_for
    start_live_duel(duel)

    from core.push_events import push_duel_joined
    push_duel_joined(duel.creator, request.user)

    return JsonResponse({
        'code':    duel.code,
        'subject': duel.subject,
        'total':   len(duel.questions),
        'creator': display_name_for(request.user, duel.creator),
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
    is_ghost = duel.is_ghost_opponent
    if not is_creator and not is_challenger and not (is_ghost and is_creator):
        return JsonResponse({'error': 'Accès refusé'}, status=403)

    if duel.status == 'waiting' and duel.is_expired():
        duel.status = 'expired'
        duel.save(update_fields=['status'])

    if is_ghost:
        q_idx = int(request.GET.get('q_idx', 0))
        from core.matchmaking import sync_ghost_duel_state
        sync_ghost_duel_state(duel, q_idx)
        duel.refresh_from_db()

    my_score      = duel.creator_score    if is_creator else duel.challenger_score
    opp_score     = duel.challenger_score if is_creator else duel.creator_score
    my_finished   = duel.creator_finished    if is_creator else duel.challenger_finished
    opp_finished  = duel.challenger_finished if is_creator else duel.creator_finished

    opponent_name = ''
    opponent_emoji = ''
    if duel.is_ghost_opponent:
        opponent_name = duel.ghost_display_name or 'Génie Fantôme'
        opponent_emoji = duel.ghost_avatar_emoji or '🎓'
    elif is_creator and duel.challenger:
        from accounts.names import display_name_for
        opponent_name = display_name_for(request.user, duel.challenger)
    elif is_challenger:
        from accounts.names import display_name_for
        opponent_name = display_name_for(request.user, duel.creator)

    return JsonResponse({
        'status':        duel.status,
        'my_score':      my_score,
        'opp_score':     opp_score,
        'my_finished':   my_finished,
        'opp_finished':  opp_finished if not is_ghost else duel.challenger_finished,
        'opponent_name': opponent_name,
        'opponent_emoji': opponent_emoji,
        'is_ghost':      is_ghost,
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
    elif duel.is_ghost_opponent and duel.creator_finished:
        plan = duel.ghost_answer_plan or []
        duel.challenger_score = sum(1 for p in plan if p.get('correct'))
        duel.challenger_finished = True
        duel.status = 'finished'

    duel.save()
    if duel.status == 'finished' and duel.challenger_id and not duel.is_ghost_opponent:
        from core.push_events import push_duel_finished
        creator_won = (duel.creator_score or 0) > (duel.challenger_score or 0)
        challenger_won = (duel.challenger_score or 0) > (duel.creator_score or 0)
        if is_creator:
            push_duel_finished(duel.challenger, duel.creator, challenger_won)
        else:
            push_duel_finished(duel.creator, duel.challenger, creator_won)
    return JsonResponse({'ok': True, 'status': duel.status})



# ─────────────────────────────────────────────
# MATCH ARENA — hub + matchmaking
# ─────────────────────────────────────────────

@login_required
def match_view(request):
    """Hub Match : quick match, privé, tournois Génies."""
    from django.db.models import Count, Q
    from core.genius.constants import competition_status_label, registration_status_label
    from core.genius.models import GeniusCompetition, GeniusRegistration
    from core.genius.services import bracket_payload
    from core.genius.services import get_user_active_team
    from core.matchmaking import online_players_count, recent_duels_for_user

    team = get_user_active_team(request.user)
    comps = list(
        GeniusCompetition.objects.filter(
            status__in=['registration', 'roster_locked', 'in_progress'],
        ).annotate(
            registered_count=Count(
                'registrations',
                filter=Q(registrations__status__in=['registered', 'roster_locked']),
            ),
        ).order_by('-start_date', '-created_at')[:12]
    )
    reg_by_comp = {}
    if team and comps:
        for reg in GeniusRegistration.objects.filter(
            competition_id__in=[c.pk for c in comps], team=team,
        ).only('status', 'competition_id'):
            reg_by_comp[reg.competition_id] = reg

    competitions = []
    for comp in comps:
        reg = reg_by_comp.get(comp.id)
        bracket = bracket_payload(comp) if comp.status == 'in_progress' else []
        rounds = {}
        for node in bracket:
            rounds.setdefault(node['round_order'], []).append(node)
        mini_bracket = []
        if rounds:
            max_round = max(rounds.keys())
            for ro in sorted(rounds.keys()):
                if ro >= max_round - 2 or len(rounds) <= 3:
                    mini_bracket.append({'round_order': ro, 'nodes': rounds[ro]})

        competitions.append({
            'id': comp.id,
            'name': comp.name,
            'status': comp.status,
            'status_label': competition_status_label(comp.status),
            'start_date': comp.start_date,
            'registered_count': getattr(comp, 'registered_count', 0) or 0,
            'my_registration': registration_status_label(reg.status) if reg else None,
            'mini_bracket': mini_bracket,
        })

    user_subjs = _get_user_serie_subjects(request.user)
    serie_subjects = [k for k in MATS.keys() if k in user_subjs]
    subject_choices = [
        {'key': 'aleatoire', 'label': 'Aléatoire', 'color': '#a78bfa'},
    ] + [
        {'key': k, 'label': MATS[k].get('label', k), 'color': MATS[k].get('color', '#10b981')}
        for k in (serie_subjects or list(MATS.keys())[:6])
    ]

    return render(request, 'core/match.html', {
        'mats': MATS,
        'serie_subjects': serie_subjects,
        'subject_choices': subject_choices,
        'online_count': online_players_count(),
        'recent_duels': recent_duels_for_user(request.user),
        'competitions': competitions,
        'has_genius_team': team is not None,
        'genius_team_name': team.name if team else '',
    })


@login_required
@require_POST
def api_match_quick(request):
    """Entrer en file ou matcher immédiatement."""
    from core.matchmaking import enter_quick_match

    try:
        data = json.loads(request.body)
        subject = (data.get('subject') or 'maths').strip()
    except Exception:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    if subject != 'aleatoire' and subject not in MATS:
        return JsonResponse({'error': 'Matière invalide'}, status=400)

    result = enter_quick_match(request.user, subject)
    if result.get('error'):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@login_required
def api_match_poll(request):
    """Poll file d'attente — pairing humain uniquement."""
    from core.matchmaking import poll_quick_match

    queue_id = request.GET.get('queue_id')
    if not queue_id:
        return JsonResponse({'error': 'queue_id requis'}, status=400)
    try:
        queue_id = int(queue_id)
    except ValueError:
        return JsonResponse({'error': 'queue_id invalide'}, status=400)

    result = poll_quick_match(request.user, queue_id)
    if result.get('error'):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@login_required
@require_POST
def api_match_cancel(request):
    from core.matchmaking import cancel_user_queue

    cancel_user_queue(request.user)
    return JsonResponse({'ok': True})


@login_required
def api_match_live_state(request):
    from core.models import QuizDuel
    from core.live_duel import live_state_payload

    code = request.GET.get('code', '').strip().upper()
    if not code:
        return JsonResponse({'error': 'code requis'}, status=400)
    try:
        duel = QuizDuel.objects.get(code=code)
    except QuizDuel.DoesNotExist:
        return JsonResponse({'error': 'introuvable'}, status=404)

    payload = live_state_payload(duel, request.user)
    if payload.get('error'):
        return JsonResponse(payload, status=403)
    return JsonResponse(payload)


@login_required
@require_POST
def api_match_live_answer(request):
    from core.models import QuizDuel
    from core.live_duel import submit_live_answer, live_state_payload

    try:
        data = json.loads(request.body)
        code = (data.get('code') or '').strip().upper()
        choice = data.get('choice')
    except Exception:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    try:
        duel = QuizDuel.objects.get(code=code)
    except QuizDuel.DoesNotExist:
        return JsonResponse({'error': 'introuvable'}, status=404)

    result = submit_live_answer(duel, request.user, choice)
    state = live_state_payload(duel, request.user)
    return JsonResponse({**state, 'answer': result})


# ─────────────────────────────────────────────

@login_required
@require_POST
def api_set_language(request):
    """Mise à jour de la langue étrangère via sidebar."""
    import json as _json
    try:
        body = _json.loads(request.body)
        langue = body.get('langue', '').strip().lower()
        if langue not in ('anglais', 'espagnol'):
            return JsonResponse({'ok': False, 'error': 'Langue invalide'}, status=400)
        profile = request.user.profile
        profile.langue_etrangere = langue
        profile.save(update_fields=['langue_etrangere'])
        return JsonResponse({'ok': True, 'langue': langue})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)



def api_user_profile_summary(request, user_id):
    """GET: résumé public (pas d'infos sensibles). Guest OK en lecture."""
    from django.contrib.auth.models import User as DUser
    from accounts.models import Friendship
    import datetime

    if not request.user.is_authenticated and not request.session.get('guest_mode'):
        return JsonResponse({'error': 'auth required'}, status=401)

    try:
        target = DUser.objects.select_related('profile').get(id=user_id)
    except DUser.DoesNotExist:
        return JsonResponse({'error': 'Utilisateur introuvable'}, status=404)

    try:
        profile = target.profile
        prenom = profile.first_name or target.first_name or ''
        nom = profile.last_name or target.last_name or ''
        ecole = profile.school or ''
        serie = profile.serie or ''
        is_premium_user = getattr(profile, 'is_premium', False) or (profile.plan_expiration and profile.plan_expiration >= datetime.date.today())
        photo_url = profile.avatar.url if getattr(profile, 'avatar', None) and profile.avatar else None
    except Exception:
        prenom = target.first_name or ''
        nom = target.last_name or ''
        ecole = ''
        serie = ''
        is_premium_user = False
        photo_url = None

    # Calculer point fort et point faible
    point_fort = None
    point_faible = None
    try:
        blended_map = _compute_all_blended_scores(target)
        if blended_map:
            valid_scores = []
            for k, info in blended_map.items():
                val = info.get('blended')
                if val is not None:
                    valid_scores.append((k, val))
            
            if valid_scores:
                valid_scores.sort(key=lambda x: x[1], reverse=True)
                k_fort, v_fort = valid_scores[0]
                point_fort = {'matiere': MATS.get(k_fort, {}).get('label', k_fort), 'pct': int(v_fort)}
                if len(valid_scores) > 1:
                    k_faible, v_faible = valid_scores[-1]
                    point_faible = {'matiere': MATS.get(k_faible, {}).get('label', k_faible), 'pct': int(v_faible)}
    except Exception:
        pass

    # Relation
    relation = 'aucune'
    if request.user.is_authenticated and target.id == request.user.id:
        relation = 'moi'
    elif request.user.is_authenticated:
        try:
            f = Friendship.objects.filter(
                models.Q(from_user=request.user, to_user=target) |
                models.Q(from_user=target, to_user=request.user)
            ).first()
            if f:
                if f.status == 'accepted':
                    relation = 'ami'
                elif f.from_user == request.user:
                    relation = 'invitation_envoyee'
                else:
                    relation = 'invitation_recue'
        except Exception:
            pass

    from accounts.names import display_name_for, public_name, alias_map_for
    aliases = alias_map_for(request.user) if request.user.is_authenticated else {}
    real_name = public_name(target)
    display = display_name_for(request.user, target, aliases) if request.user.is_authenticated else real_name
    alias = aliases.get(target.id, '') if relation == 'ami' else ''

    return JsonResponse({
        'ok': True,
        'user_id': target.id,
        'prenom': prenom,
        'nom': nom,
        'username': target.username,
        'display_name': display,
        'alias': alias,
        'real_name': real_name,
        'ecole': ecole,
        'serie': serie,
        'is_premium': bool(is_premium_user),
        'photo_url': photo_url,
        'point_fort': point_fort,
        'point_faible': point_faible,
        'relation': relation,
    })


@login_required
@require_POST
def api_group_chat_quiz_attempt(request):
    """POST: correction Extra bèt (sans IA) d'un quiz posté dans le groupe."""
    from accounts.models import GroupMessage, GroupChatQuizAttempt
    from core.extra_bet_grader import grade_extra_bet_answer

    data, _err = _parse_json_body(request)
    if _err:
        return _err

    message_id = data.get('message_id')
    answer_raw = data.get('answer', '')
    if isinstance(answer_raw, dict):
        submitted_answer = json.dumps(answer_raw, ensure_ascii=False)
    else:
        submitted_answer = str(answer_raw or '').strip()

    try:
        msg = GroupMessage.objects.get(id=message_id)
    except GroupMessage.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    if not msg.quiz_data:
        return JsonResponse({'error': "Ce message n'est pas un quiz"}, status=400)
    if not submitted_answer:
        return JsonResponse({'error': 'Réponse manquante'}, status=400)

    q = msg.quiz_data or {}
    qtype = str(q.get('question_type') or q.get('type') or 'word').strip().lower()
    qtype = _GROUP_QUIZ_TYPE_ALIASES.get(qtype, qtype)
    expected = q.get('answer', '')
    if isinstance(expected, dict):
        expected = json.dumps(expected, ensure_ascii=False)
    options = q.get('options')
    if options is None:
        options = q.get('choices') or []

    is_correct, display_answer = grade_extra_bet_answer(
        submitted_answer, str(expected or ''), qtype, options,
    )
    if is_correct:
        correction = 'Bonne réponse.'
    else:
        correction = f'La réponse attendue : {display_answer}' if display_answer else 'Réponse incorrecte.'

    GroupChatQuizAttempt.objects.update_or_create(
        message=msg,
        user=request.user,
        defaults={
            'submitted_answer': submitted_answer,
            'is_correct': is_correct,
        },
    )
    attempts_count = GroupChatQuizAttempt.objects.filter(message=msg).count()
    correct_count = GroupChatQuizAttempt.objects.filter(message=msg, is_correct=True).count()

    return JsonResponse({
        'ok': True,
        'is_correct': is_correct,
        'correct_answer': display_answer,
        'correction': correction,
        'quiz_stats': {
            'attempts': attempts_count,
            'pct': round(100 * correct_count / attempts_count) if attempts_count else 0,
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
