"""
ExamItemRegistry — déduplication stricte des items d'examen blanc.

- Hash canonique : sha256(text.strip()[:200])
- Historique utilisateur : exclure les items déjà vus (DB ou sessionStorage)
- Tirage intra-examen : jamais de duplication (interdit pool * k)
"""
from __future__ import annotations

import hashlib
import random
from typing import Any


HASH_TEXT_SLICE = 200
PASSAGE_TEXT_SLICE = 1200


def hash_item_text(text: str) -> str:
    """Hash stable pour identifier un item d'examen."""
    normalized = (text or '').strip()[:HASH_TEXT_SLICE]
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def hash_passage_text(text: str) -> str:
    """Hash plus long pour les textes de lecture (évite les collisions sur le préambule)."""
    normalized = (text or '').strip()[:PASSAGE_TEXT_SLICE]
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def item_hashes_from_text(text: str) -> list[str]:
    """Hashes court + long d'un énoncé (le long n'est ajouté que si le texte dépasse 200 car.)."""
    stripped = (text or '').strip()
    if len(stripped) < 10:
        return []
    hashes = [hash_item_text(stripped)]
    if len(stripped) > HASH_TEXT_SLICE:
        long_h = hash_passage_text(stripped)
        if long_h not in hashes:
            hashes.append(long_h)
    return hashes


def item_text_from_dict(item: Any, text_key: str = 'text') -> str:
    """Extrait le texte canonique d'un item (dict ou str)."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return (
            item.get(text_key)
            or item.get('text')
            or item.get('intro')
            or item.get('enonce')
            or item.get('texte')
            or ''
        ).strip()
    return str(item or '').strip()


def extract_exam_item_hashes(exam_data: dict) -> list[str]:
    """Parcourt un examen généré et retourne les hashes de tous les items textuels."""
    if not exam_data:
        return []
    hashes: list[str] = []
    seen: set[str] = set()

    def _add(h: str) -> None:
        if h and h not in seen:
            seen.add(h)
            hashes.append(h)

    for h in exam_data.get('passage_hashes') or []:
        _add(str(h).strip())
    for part in exam_data.get('parts') or []:
        for section in part.get('sections') or []:
            for item in section.get('items') or []:
                if isinstance(item, dict):
                    extra = item.get('source_hash')
                    if extra:
                        _add(str(extra).strip())
                txt = item_text_from_dict(item)
                if not txt or len(txt) < 10:
                    continue
                for h in item_hashes_from_text(txt):
                    _add(h)
    return hashes


class ExamItemRegistry:
    """
    Registre de tirage pour un examen :
    - exclude_hashes : historique utilisateur (items déjà vus)
    - used_hashes : items déjà tirés dans l'examen en cours
    """

    def __init__(self, exclude_hashes: set[str] | frozenset[str] | None = None):
        self.exclude_hashes: set[str] = set(exclude_hashes or ())
        self.used_hashes: set[str] = set()

    def is_available(self, text: str) -> bool:
        hs = item_hashes_from_text(text)
        if not hs:
            return False
        return all(h not in self.exclude_hashes and h not in self.used_hashes for h in hs)

    def mark_used(self, text: str) -> str:
        hs = item_hashes_from_text(text)
        if not hs:
            h = hash_item_text(text)
            self.used_hashes.add(h)
            return h
        for h in hs:
            self.used_hashes.add(h)
        return hs[0]

    def pick(self, pool: list, n: int, text_key: str = 'text') -> list:
        """
        Tire jusqu'à n items uniques depuis pool.
        STRICT : jamais de duplication — retourne moins de n si pool insuffisant.
        """
        if not pool or n <= 0:
            return []

        candidates: list[Any] = []
        for item in pool:
            txt = item_text_from_dict(item, text_key)
            if txt and self.is_available(txt):
                candidates.append(item)

        random.shuffle(candidates)
        picked: list[Any] = []
        for item in candidates:
            if len(picked) >= n:
                break
            txt = item_text_from_dict(item, text_key)
            if not txt or not self.is_available(txt):
                continue
            self.mark_used(txt)
            picked.append(item)
        return picked

    def pick_by_keywords(
        self,
        pool: list,
        keywords: list[str],
        n: int,
        text_key: str = 'text',
    ) -> list:
        """Tire n items en priorisant ceux qui matchent les mots-clés, sans duplication."""
        if not pool or n <= 0:
            return []

        kws = [k.lower() for k in keywords if k]
        scored: list[tuple[int, float, Any]] = []
        for item in pool:
            txt = item_text_from_dict(item, text_key)
            if not txt or not self.is_available(txt):
                continue
            txt_lower = txt.lower()
            score = sum(1 for k in kws if k in txt_lower)
            scored.append((score, random.random(), item))

        scored.sort(key=lambda x: (-x[0], x[1]))
        picked: list[Any] = []
        for _, _, item in scored:
            if len(picked) >= n:
                break
            txt = item_text_from_dict(item, text_key)
            if not txt or not self.is_available(txt):
                continue
            self.mark_used(txt)
            picked.append(item)
        return picked

    def all_session_hashes(self) -> list[str]:
        return list(self.used_hashes)
