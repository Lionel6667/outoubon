"""Envoi Firebase Cloud Messaging — jamais bloquant pour l'app."""
from __future__ import annotations

import json
import logging
import os
from typing import Iterable, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_app = None
_init_failed = False


def _get_app():
    global _app, _init_failed
    if _app is not None:
        return _app
    if _init_failed:
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials
    except Exception:
        logger.warning('firebase-admin n’est pas installé')
        _init_failed = True
        return None
    if firebase_admin._apps:
        _app = firebase_admin.get_app()
        return _app
    path = getattr(settings, 'FIREBASE_CREDENTIALS_PATH', '') or ''
    from pathlib import Path
    raw_json = os.getenv('FIREBASE_CREDENTIALS_JSON', '').strip()
    try:
        if raw_json:
            info = json.loads(raw_json)
            cred = credentials.Certificate(info)
        elif path and Path(path).is_file():
            cred = credentials.Certificate(path)
        else:
            logger.warning('Credentials Firebase introuvables')
            _init_failed = True
            return None
        _app = firebase_admin.initialize_app(cred)
        return _app
    except Exception:
        logger.exception('Init Firebase Admin impossible')
        _init_failed = True
        return None


def _abs_url(path: str) -> str:
    base = (getattr(settings, 'PUBLIC_BASE_URL', '') or 'https://outoubon.com').rstrip('/')
    path = path or '/dashboard/'
    if path.startswith('http://') or path.startswith('https://'):
        return path
    if not path.startswith('/'):
        path = '/' + path
    return base + path


def send_to_users(
    user_ids: Iterable[int],
    *,
    title: str,
    body: str,
    url: str = '/dashboard/',
    kind: str = 'generic',
    collapse_key: str = '',
    exclude_ids: Optional[Iterable[int]] = None,
) -> int:
    """Envoie une push aux appareils des user_ids. Retourne le nb d’envois OK."""
    ids = {int(i) for i in user_ids if i}
    if exclude_ids:
        ids -= {int(i) for i in exclude_ids if i}
    if not ids:
        return 0
    if _get_app() is None:
        return 0
    from accounts.models import PushDevice
    from firebase_admin import messaging

    tokens = list(
        PushDevice.objects.filter(user_id__in=ids, enabled=True)
        .values_list('token', flat=True)
    )
    if not tokens:
        return 0

    abs_url = _abs_url(url)
    icon = _abs_url('/static/img/logo.png')
    data = {
        'url': abs_url,
        'kind': kind,
        'title': (title or '')[:120],
        'body': (body or '')[:180],
    }
    webpush = messaging.WebpushConfig(
        headers={'TTL': '86400'},
        fcm_options=messaging.WebpushFCMOptions(link=abs_url),
        notification=messaging.WebpushNotification(
            icon=icon,
            badge=icon,
        ),
    )
    android = messaging.AndroidConfig(collapse_key=(collapse_key or kind)[:40] or None)

    sent = 0
    dead = []
    for i in range(0, len(tokens), 500):
        chunk = tokens[i:i + 500]
        multicast = messaging.MulticastMessage(
            tokens=chunk,
            data=data,
            webpush=webpush,
            android=android,
        )
        try:
            resp = messaging.send_each_for_multicast(multicast, dry_run=False)
        except Exception:
            logger.exception('FCM send_each_for_multicast failed (%s)', kind)
            continue
        sent += int(getattr(resp, 'success_count', 0) or 0)
        responses = getattr(resp, 'responses', None) or []
        for token, item in zip(chunk, responses):
            if item.success:
                continue
            exc = getattr(item, 'exception', None)
            code = ''
            try:
                code = str(getattr(exc, 'code', '') or '')
            except Exception:
                code = ''
            text = f'{code} {exc}'.lower()
            if any(s in text for s in (
                'unregistered', 'registration-token-not-registered',
                'invalid-argument', 'not-found', 'requested entity was not found',
            )):
                dead.append(token)
    if dead:
        PushDevice.objects.filter(token__in=dead).delete()
    return sent
