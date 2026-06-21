"""
Management command : clean_subject_json
=========================================
Utilise l'IA pour nettoyer et structurer le texte brut OCR de chaque examen
dans les fichiers database/json/exams_*.json

Stratégie : traitement par chunks de 1100 chars pour contourner la limite du modèle
(openai/gpt-oss-20b retourne vide sur les longs textes — chunks courts = réponses fiables)

Usage :
    python manage.py clean_subject_json --subject physique
    python manage.py clean_subject_json --subject physique --limit 10
    python manage.py clean_subject_json --all
    python manage.py clean_subject_json --subject physique --redo   # refait les déjà nettoyés

Chaque examen traité reçoit un champ "cleaned": true.
La sauvegarde est incrémentale (après chaque examen) → reprise possible.
"""
import json
import time
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

# Import du client IA interne
from core.gemini import _client

# Modèle disponible sur ce compte Groq
CLEAN_MODEL = 'openai/gpt-oss-20b'

# Taille max par chunk (le modèle répond de façon fiable sur ~1100 chars)
CHUNK_SIZE = 1100


def _split_chunks(text: str, size: int = CHUNK_SIZE) -> list:
    """Découpe le texte en chunks en coupant sur des sauts de ligne si possible."""
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Cherche un saut de ligne proche pour couper proprement
            nl = text.rfind('\n', start, end)
            if nl > start + size // 2:
                end = nl + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def _clean_chunk(chunk: str, subject: str, chunk_idx: int, total_chunks: int,
                 year: str, series: list, client) -> str:
    """Nettoie un chunk de texte OCR via l'IA. Retourne '' en cas d'échec."""
    series_str = ', '.join(series) if series else 'inconnue'
    is_first = (chunk_idx == 0)

    instructions = (
        "Corrige les erreurs OCR (caractères corrompus comme ?, lettres collées, "
        "mots coupés en fin de ligne). Remplace les ? par les bons accents français "
        "(é, è, ê, à, â, ô, î, ù, û, ç). Supprime les artefacts PDF (séries de tirets, "
        "lignes de points). Garde TOUT le contenu, tous les chiffres et formules. "
        "Réponds UNIQUEMENT avec le texte corrigé, sans commentaire."
    )
    if is_first and total_chunks > 1:
        instructions += (
            " C'est le début de l'examen : assure-toi que l'en-tête "
            "(MINISTÈRE, FILIÈRE, SÉRIE, date, consignes) est bien formaté."
        )

    prompt = (
        f"Examen de {subject.upper()} bac haïtien {year or ''} série {series_str}. "
        f"Partie {chunk_idx+1}/{total_chunks}.\n\n"
        f"{instructions}\n\n"
        f"TEXTE OCR:\n{chunk}"
    )

    try:
        resp = client.chat.completions.create(
            model=CLEAN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1400,
            temperature=0.1,
        )
        return (resp.choices[0].message.content or '').strip()
    except Exception as e:
        return ''


def _clean_exam_text(raw_text: str, subject: str, year: str, series: list,
                     delay_between_chunks: float = 0.8) -> str:
    """
    Nettoie le texte OCR d'un examen complet par chunks.
    Retourne '' si tous les chunks échouent.
    """
    chunks = _split_chunks(raw_text[:5500])  # limite à 5500 chars max
    client = _client()
    cleaned_parts = []
    fails = 0

    for idx, chunk in enumerate(chunks):
        result = _clean_chunk(chunk, subject, idx, len(chunks), year, series, client)
        if result:
            cleaned_parts.append(result)
        else:
            # Chunk échoué : garde l'original
            cleaned_parts.append(chunk)
            fails += 1
        # Pause entre chunks (rate limit)
        if idx < len(chunks) - 1:
            time.sleep(delay_between_chunks)

    if fails == len(chunks):
        return ''  # Tous les chunks ont échoué

    return '\n\n'.join(cleaned_parts)


class Command(BaseCommand):
    help = 'Nettoie et structure les textes OCR dans exams_*.json via IA'

    def add_arguments(self, parser):
        parser.add_argument('--subject', type=str, default='',
                            help='Matière à traiter (ex: physique)')
        parser.add_argument('--all', action='store_true',
                            help='Traite toutes les matières')
        parser.add_argument('--limit', type=int, default=0,
                            help='Nombre max d\'examens à traiter par matière (0=tous)')
        parser.add_argument('--redo', action='store_true',
                            help='Refait les examens déjà nettoyés')
        parser.add_argument('--delay', type=float, default=1.5,
                            help='Délai en secondes entre chaque appel IA (défaut: 1.5)')

    def handle(self, *args, **options):
        db_path = Path(getattr(settings, 'COURSE_DB_PATH', ''))
        if not db_path or not db_path.exists():
            self.stderr.write(self.style.ERROR(f'COURSE_DB_PATH introuvable : {db_path}'))
            return

        json_dir = db_path / 'json'
        if not json_dir.exists():
            self.stderr.write(self.style.ERROR('database/json/ introuvable. Lance d\'abord build_subject_json'))
            return

        subject   = options.get('subject', '').strip().lower()
        do_all    = options.get('all', False)
        limit     = options.get('limit', 0)
        redo      = options.get('redo', False)
        delay     = options.get('delay', 1.5)

        # Sélectionner les fichiers à traiter
        if do_all:
            files = sorted(json_dir.glob('exams_*.json'))
        elif subject:
            f = json_dir / f'exams_{subject}.json'
            if not f.exists():
                self.stderr.write(self.style.ERROR(f'Fichier introuvable : {f}'))
                return
            files = [f]
        else:
            self.stderr.write(self.style.ERROR('Spécifie --subject NOM ou --all'))
            return

        total_cleaned = 0
        total_skipped = 0

        for json_file in files:
            subj = json_file.stem.replace('exams_', '')
            self.stdout.write(f'\n{"="*60}')
            self.stdout.write(f'[SUJET] Traitement : {json_file.name}')

            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            exams: list = data.get('exams', [])
            to_process = [e for e in exams if redo or not e.get('cleaned')]

            if limit > 0:
                to_process = to_process[:limit]

            already_done = sum(1 for e in exams if e.get('cleaned') and not redo)
            self.stdout.write(
                f'  {len(exams)} examens total | {already_done} déjà nettoyés | '
                f'{len(to_process)} à traiter'
            )

            if not to_process:
                self.stdout.write(self.style.SUCCESS('  OK Deja tout nettoye !'))
                continue

            for i, exam in enumerate(to_process, 1):
                fname  = exam.get('file', '?')
                year   = exam.get('year', '')
                series = exam.get('series', [])
                raw    = exam.get('text', '')

                self.stdout.write(
                    f'  [{i:3d}/{len(to_process)}] {fname[:55]:<55} ',
                    ending=''
                )

                if not raw or len(raw) < 50:
                    self.stdout.write(self.style.WARNING('WARN Texte trop court, ignore'))
                    total_skipped += 1
                    continue

                # Texte déjà propre ? (peu ou pas de caractères U+FFFD = remplacement OCR)
                fffd_ratio = raw.count('\ufffd') / max(len(raw), 1)
                if fffd_ratio < 0.02 and not redo:  # moins de 2% de chars corrompus
                    exam['text']     = raw
                    exam['cleaned']  = True
                    exam['chars']    = len(raw)
                    exam['auto_clean'] = True  # marqué propre sans appel IA
                    total_cleaned += 1
                    data['total_chars'] = sum(len(e.get('text', '')) for e in exams)
                    with open(json_file, 'w', encoding='utf-8') as fp:
                        json.dump(data, fp, ensure_ascii=False, separators=(',', ':'))
                    self.stdout.write(self.style.SUCCESS(f'PROPRE {len(raw):,} chars (texte deja correct, sans IA)'))
                    if i < len(to_process):
                        time.sleep(0.1)  # pas besoin d'attendre l'IA
                    continue

                t0 = time.time()
                # delay entre chunks = 60% du délai global (le reste sépare les examens)
                chunk_delay = max(0.5, delay * 0.6)
                cleaned = _clean_exam_text(raw, subj, year, series,
                                           delay_between_chunks=chunk_delay)
                elapsed = round(time.time() - t0, 1)

                # Validation : le résultat doit être substantiel (≥30% de l'original)
                min_len = max(200, len(raw) * 0.30)
                if cleaned.startswith('[ERREUR') or len(cleaned) < min_len:
                    reason = 'Erreur IA' if cleaned.startswith('[ERREUR') else f'Trop court ({len(cleaned)} chars)'
                    self.stdout.write(self.style.ERROR(f'ECHEC {reason} ({elapsed}s) - texte original conserve'))
                    total_skipped += 1
                else:
                    # Mettre à jour dans la liste d'examens
                    exam['text']       = cleaned
                    exam['text_raw']   = raw          # garde l'original en backup
                    exam['cleaned']    = True
                    exam['chars']      = len(cleaned)
                    total_cleaned += 1

                    # Recalcule les totaux
                    data['total_chars'] = sum(len(e.get('text', '')) for e in exams)

                    # Sauvegarde incrémentale
                    with open(json_file, 'w', encoding='utf-8') as fp:
                        json.dump(data, fp, ensure_ascii=False, separators=(',', ':'))

                    self.stdout.write(
                        self.style.SUCCESS(f'OK {len(cleaned):,} chars ({elapsed}s)')
                    )

                # Pause pour respecter les rate limits Groq
                if i < len(to_process):
                    time.sleep(delay)

        self.stdout.write(f'\n{"="*60}')
        self.stdout.write(self.style.SUCCESS(
            f'\nTermine : {total_cleaned} nettoyes | {total_skipped} ignores\n'
        ))
        if total_cleaned:
            self.stdout.write(
                'Les fichiers JSON mis a jour seront utilises automatiquement '
                'par pdf_loader.py au prochain rechargement.\n'
            )
