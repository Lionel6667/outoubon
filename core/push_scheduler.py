"""Lance le digest push une fois par jour vers 18h (Port-au-Prince)."""
from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import timedelta

logger = logging.getLogger(__name__)

_started = False


def _should_skip_process() -> bool:
    cmd = ' '.join(sys.argv).lower()
    skip = (
        'migrate', 'makemigrations', 'check', 'shell', 'test',
        'collectstatic', 'send_push_digests',
    )
    return any(s in cmd for s in skip)


def _run_digest():
    try:
        from django.core.management import call_command
        call_command('send_push_digests')
    except Exception:
        logger.exception('Digest push du soir impossible')


def _loop():
    from django.core.cache import cache
    from django.utils import timezone

    while True:
        try:
            now = timezone.localtime()
            today = now.date().isoformat()
            lock = f'push_digest_lock:{today}'
            target = now.replace(hour=18, minute=0, second=0, microsecond=0)
            if now >= target:
                if cache.add(lock, 1, 20 * 3600):
                    _run_digest()
                target = target + timedelta(days=1)
            delay = max(30.0, (target - timezone.localtime()).total_seconds())
            delay = min(delay, 30 * 60)
        except Exception:
            logger.exception('Scheduler push')
            delay = 15 * 60
        threading.Event().wait(delay)


def start_push_digest_scheduler():
    global _started
    if _started or _should_skip_process():
        return
    is_runserver = 'runserver' in sys.argv
    if is_runserver and os.environ.get('RUN_MAIN') != 'true':
        return
    _started = True
    t = threading.Thread(target=_loop, name='otb-push-digest', daemon=True)
    t.start()
