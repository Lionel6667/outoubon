"""
Garde-fous coût DeepSeek : caps de prompt et compression d'images.
Le thinking OFF est appliqué dans gemini._tracked_create.
"""
from __future__ import annotations

_MAX_SINGLE_MSG_CHARS = 8_000
_MAX_TOTAL_MSG_CHARS = 18_000
_MAX_IMAGE_BYTES = 220_000
_MAX_IMAGE_SIDE = 1024


def prepare_image_bytes(image_data: bytes | None, image_mime: str | None = None):
    """Réduit une image avant l'API vision (photos HD = 50k–100k tokens)."""
    if not image_data:
        return None, None
    mime = image_mime or 'image/jpeg'
    if len(image_data) <= _MAX_IMAGE_BYTES:
        return image_data, mime
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(image_data))
        img.thumbnail((_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        out = None
        for quality in (65, 45):
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=quality, optimize=True)
            out = buf.getvalue()
            if len(out) <= _MAX_IMAGE_BYTES:
                break
        if out and len(out) <= _MAX_IMAGE_BYTES * 2:
            return out, 'image/jpeg'
    except Exception:
        pass
    return None, None


def _content_chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for part in content:
            if isinstance(part, dict) and part.get('type') == 'text':
                n += len(str(part.get('text', '')))
        return n
    return 0


def _truncate_one(m: dict) -> dict:
    nm = dict(m)
    content = nm.get('content')
    if isinstance(content, str) and len(content) > _MAX_SINGLE_MSG_CHARS:
        nm['content'] = content[:_MAX_SINGLE_MSG_CHARS].rstrip() + '\n…'
    elif isinstance(content, list):
        new_parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'text':
                text = str(part.get('text') or '')
                if len(text) > _MAX_SINGLE_MSG_CHARS:
                    text = text[:_MAX_SINGLE_MSG_CHARS] + '…'
                new_parts.append({**part, 'text': text})
            elif part.get('type') == 'image_url':
                url = str((part.get('image_url') or {}).get('url') or '')
                if len(url) > 400_000:
                    continue
                new_parts.append(part)
            else:
                new_parts.append(part)
        nm['content'] = new_parts if new_parts else 'Analyse ceci.'
    return nm


def cap_messages(messages: list) -> list:
    """Garde le préfixe cache (1–2 system) identique ; coupe l'historique ensuite.

    DeepSeek met en cache le préfixe byte-identique depuis le token 0.
    Message 1 = prompt statique (jamais tronqué). Message 2 = notes de chapitre
    (troncature identique à chaque appel si trop long).
    """
    if not messages:
        return messages
    cleaned = [m for m in messages if isinstance(m, dict)]
    if not cleaned:
        return messages

    prefix = []
    rest = []
    for i, m in enumerate(cleaned):
        leading_system = (
            m.get('role') == 'system'
            and len(prefix) < 2
            and (i == 0 or (prefix and cleaned[i - 1].get('role') == 'system'))
        )
        if leading_system:
            prefix.append(m if not prefix else _truncate_one(m))
        else:
            rest.append(_truncate_one(m))

    total = sum(_content_chars(m.get('content')) for m in prefix + rest)
    if total <= _MAX_TOTAL_MSG_CHARS:
        return prefix + rest

    while rest and total > _MAX_TOTAL_MSG_CHARS:
        drop_idx = None
        last_idx = len(rest) - 1
        for i, m in enumerate(rest):
            if i == last_idx:
                continue
            if m.get('role') in ('user', 'assistant'):
                drop_idx = i
                break
        if drop_idx is None:
            for i in range(max(0, len(rest) - 1)):
                drop_idx = i
                break
        if drop_idx is None:
            break
        dropped = rest.pop(drop_idx)
        total -= _content_chars(dropped.get('content'))
    return prefix + rest
