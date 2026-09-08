"""
Suivi centralisé des appels DeepSeek : plafonds par utilisateur, invité et global.
Micro-log journalier par feature pour monitoring marge brute.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Sum

logger = logging.getLogger(__name__)

_context = threading.local()

MAX_AI_API_CALLS_PER_DAY = int(getattr(settings, 'MAX_AI_API_CALLS_PER_DAY', 50))
MAX_GUEST_AI_API_CALLS_PER_DAY = int(getattr(settings, 'MAX_GUEST_AI_API_CALLS_PER_DAY', 10))
MAX_GLOBAL_API_CALLS_PER_DAY = int(getattr(settings, 'MAX_GLOBAL_API_CALLS_PER_DAY', 600))
MAX_GLOBAL_TOKENS_PER_DAY = int(getattr(settings, 'MAX_GLOBAL_TOKENS_PER_DAY', 2_500_000))
ENABLE_QUIZ_BACKGROUND_SEED = bool(getattr(settings, 'ENABLE_QUIZ_BACKGROUND_SEED', False))
ENABLE_EXAM_AI_ENHANCE = bool(getattr(settings, 'ENABLE_EXAM_AI_ENHANCE', False))

# Coût estimé DeepSeek V4-Flash (USD / million tokens)
_PRICE_INPUT_PER_M = float(getattr(settings, 'AI_PRICE_INPUT_PER_M', 0.14))
_PRICE_OUTPUT_PER_M = float(getattr(settings, 'AI_PRICE_OUTPUT_PER_M', 0.28))
_PRICE_CACHE_HIT_PER_M = float(getattr(settings, 'AI_PRICE_CACHE_HIT_PER_M', 0.0028))

_CACHE_TTL = int(timedelta(days=1).total_seconds())
AI_MICROLOG_ENABLED = bool(getattr(settings, 'AI_MICROLOG_ENABLED', True))


class AiBudgetExceeded(Exception):
    """Plafond IA atteint (utilisateur, invité ou global)."""

    def __init__(self, reason: str = 'budget'):
        self.reason = reason
        super().__init__(reason)


def _today_key() -> str:
    return date.today().isoformat()


def infer_feature_from_path(path: str) -> str:
    """Déduit la feature IA depuis l'URL de la requête."""
    p = (path or '').lower()
    if p.rstrip('/').endswith('/api/chat') or '/api/chat/' in p:
        return 'chat'
    if any(x in p for x in ('/api/exercices/', '/api/exercise')):
        return 'exercise'
    if any(x in p for x in ('/api/exam/', '/api/examen-blanc/', '/examen-blanc')):
        return 'exam'
    if '/api/fiches/' in p:
        return 'fiches'
    if any(x in p for x in ('/api/cours/', '/api/course-question/', '/api/chapter-summary/')):
        return 'course'
    if '/api/quiz/' in p:
        return 'quiz'
    return 'other'


def set_ai_context(*, user=None, guest_key: str | None = None, feature: str | None = None) -> None:
    _context.user = user
    _context.guest_key = guest_key
    _context.feature = feature or 'other'


def clear_ai_context() -> None:
    _context.user = None
    _context.guest_key = None
    _context.feature = None


def get_ai_context_user():
    return getattr(_context, 'user', None)


def get_ai_context_guest_key() -> str | None:
    return getattr(_context, 'guest_key', None)


def get_ai_context_feature() -> str:
    return getattr(_context, 'feature', None) or 'other'


def _global_calls_key() -> str:
    return f'ai_global_calls_{_today_key()}'


def _global_tokens_key() -> str:
    return f'ai_global_tokens_{_today_key()}'


def _guest_calls_key(guest_key: str) -> str:
    return f'ai_guest_calls_{guest_key}_{_today_key()}'


def estimate_cost_usd_micro(
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int = 0,
) -> int:
    """Retourne le coût estimé en millionièmes de USD."""
    miss = max(0, prompt_tokens - cache_hit_tokens)
    cost = (
        (miss / 1_000_000) * _PRICE_INPUT_PER_M
        + (cache_hit_tokens / 1_000_000) * _PRICE_CACHE_HIT_PER_M
        + (completion_tokens / 1_000_000) * _PRICE_OUTPUT_PER_M
    )
    return max(0, int(round(cost * 1_000_000)))


def get_global_usage() -> dict:
    return {
        'api_calls': int(cache.get(_global_calls_key(), 0) or 0),
        'tokens': int(cache.get(_global_tokens_key(), 0) or 0),
    }


def get_guest_api_calls(guest_key: str) -> int:
    return int(cache.get(_guest_calls_key(guest_key), 0) or 0)


def get_user_usage_today(user) -> dict:
    """Agrégat tokens/coût du jour pour un utilisateur connecté."""
    from core.models import AiUsageDaily

    today = date.today()
    rows = AiUsageDaily.objects.filter(user=user, date=today)
    agg = rows.aggregate(
        api_calls=Sum('api_calls'),
        prompt_tokens=Sum('prompt_tokens'),
        completion_tokens=Sum('completion_tokens'),
        cost_usd_micro=Sum('cost_usd_micro'),
    )
    by_feature = {
        r['feature']: {
            'api_calls': r['api_calls'] or 0,
            'prompt_tokens': r['prompt_tokens'] or 0,
            'completion_tokens': r['completion_tokens'] or 0,
            'cost_usd': round((r['cost_usd_micro'] or 0) / 1_000_000, 6),
        }
        for r in rows.values('feature', 'api_calls', 'prompt_tokens', 'completion_tokens', 'cost_usd_micro')
    }
    total_cost_micro = agg['cost_usd_micro'] or 0
    return {
        'date': today.isoformat(),
        'api_calls': agg['api_calls'] or 0,
        'prompt_tokens': agg['prompt_tokens'] or 0,
        'completion_tokens': agg['completion_tokens'] or 0,
        'total_tokens': (agg['prompt_tokens'] or 0) + (agg['completion_tokens'] or 0),
        'cost_usd': round(total_cost_micro / 1_000_000, 6),
        'by_feature': by_feature,
    }


def get_margin_snapshot_today() -> dict:
    """Vue globale du jour pour monitoring marge (staff)."""
    from core.models import AiUsageDaily

    today = date.today()
    qs = AiUsageDaily.objects.filter(date=today)
    agg = qs.aggregate(
        api_calls=Sum('api_calls'),
        prompt_tokens=Sum('prompt_tokens'),
        completion_tokens=Sum('completion_tokens'),
        cache_hit_tokens=Sum('cache_hit_tokens'),
        cost_usd_micro=Sum('cost_usd_micro'),
    )
    by_feature = list(
        qs.values('feature')
        .annotate(
            api_calls=Sum('api_calls'),
            prompt_tokens=Sum('prompt_tokens'),
            completion_tokens=Sum('completion_tokens'),
            cache_hit_tokens=Sum('cache_hit_tokens'),
            cost_usd_micro=Sum('cost_usd_micro'),
        )
        .order_by('-cost_usd_micro')
    )
    for row in by_feature:
        row['cost_usd'] = round((row.pop('cost_usd_micro') or 0) / 1_000_000, 4)
        row['total_tokens'] = (row['prompt_tokens'] or 0) + (row['completion_tokens'] or 0)
    top_users = list(
        qs.filter(user__isnull=False)
        .values('user_id', 'user__username')
        .annotate(
            cost_usd_micro=Sum('cost_usd_micro'),
            total_tokens=Sum(F('prompt_tokens') + F('completion_tokens')),
        )
        .order_by('-cost_usd_micro')[:15]
    )
    for row in top_users:
        row['cost_usd'] = round((row.pop('cost_usd_micro') or 0) / 1_000_000, 4)
    total_cost_micro = agg['cost_usd_micro'] or 0
    return {
        'date': today.isoformat(),
        'api_calls': agg['api_calls'] or 0,
        'prompt_tokens': agg['prompt_tokens'] or 0,
        'completion_tokens': agg['completion_tokens'] or 0,
        'cache_hit_tokens': agg.get('cache_hit_tokens') or 0,
        'total_tokens': (agg['prompt_tokens'] or 0) + (agg['completion_tokens'] or 0),
        'cost_usd': round(total_cost_micro / 1_000_000, 4),
        'by_feature': by_feature,
        'top_users': top_users,
        'global_cache': get_global_usage(),
    }


_FEATURE_LABELS = {
    'chat': 'Chat tuteur',
    'exercise': 'Exercices',
    'exam': 'Examen blanc',
    'fiches': 'Fiches mémo',
    'course': 'Cours interactif',
    'quiz': 'Quiz',
    'other': 'Autre',
}
_BALANCE_CACHE_KEY = 'deepseek_balance_v1'
_BALANCE_CACHE_TTL = 45


def _usd(micro: int) -> float:
    return round((micro or 0) / 1_000_000, 4)


def _serialize_period(qs) -> dict:
    agg = qs.aggregate(
        api_calls=Sum('api_calls'),
        prompt_tokens=Sum('prompt_tokens'),
        completion_tokens=Sum('completion_tokens'),
        cache_hit_tokens=Sum('cache_hit_tokens'),
        cost_usd_micro=Sum('cost_usd_micro'),
    )
    prompt = int(agg['prompt_tokens'] or 0)
    completion = int(agg['completion_tokens'] or 0)
    cache_hit = int(agg['cache_hit_tokens'] or 0)
    stored_micro = int(agg['cost_usd_micro'] or 0)
    live_micro = estimate_cost_usd_micro(prompt, completion, cache_hit)
    return {
        'api_calls': int(agg['api_calls'] or 0),
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        'cache_hit_tokens': cache_hit,
        'total_tokens': prompt + completion,
        'cache_hit_pct': round(100.0 * cache_hit / prompt, 1) if prompt else 0.0,
        'cost_usd': _usd(live_micro),
        'cost_usd_recorded': _usd(stored_micro),
    }


def fetch_deepseek_balance() -> dict:
    """Solde live du compte DeepSeek (GET /user/balance). Cache 45 s."""
    cached = cache.get(_BALANCE_CACHE_KEY)
    if isinstance(cached, dict):
        return cached

    key = (getattr(settings, 'DEEPSEEK_API_KEY', '') or '').strip()
    empty = {
        'ok': False,
        'available': False,
        'currency': 'USD',
        'total_balance': None,
        'granted_balance': None,
        'topped_up_balance': None,
        'error': 'missing_key',
        'raw': [],
    }
    if not key:
        return empty

    req = urllib.request.Request(
        'https://api.deepseek.com/user/balance',
        headers={
            'Authorization': f'Bearer {key}',
            'Accept': 'application/json',
        },
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        body = ''
        try:
            body = exc.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        empty['error'] = f'http_{exc.code}'
        empty['detail'] = body
        logger.warning('DeepSeek balance HTTP %s: %s', exc.code, body)
        return empty
    except Exception as exc:
        empty['error'] = 'network'
        empty['detail'] = str(exc)[:200]
        logger.warning('DeepSeek balance fetch failed: %s', exc)
        return empty

    infos = payload.get('balance_infos') or []
    usd = next((b for b in infos if str(b.get('currency', '')).upper() == 'USD'), None)
    chosen = usd or (infos[0] if infos else {})

    def _num(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    result = {
        'ok': True,
        'available': bool(payload.get('is_available')),
        'currency': str(chosen.get('currency') or 'USD'),
        'total_balance': _num(chosen.get('total_balance')),
        'granted_balance': _num(chosen.get('granted_balance')),
        'topped_up_balance': _num(chosen.get('topped_up_balance')),
        'error': None,
        'raw': [
            {
                'currency': b.get('currency'),
                'total_balance': b.get('total_balance'),
                'granted_balance': b.get('granted_balance'),
                'topped_up_balance': b.get('topped_up_balance'),
            }
            for b in infos if isinstance(b, dict)
        ],
    }
    cache.set(_BALANCE_CACHE_KEY, result, timeout=_BALANCE_CACHE_TTL)
    return result


def get_ai_ops_dashboard(days: int = 30) -> dict:
    """Tableau de bord type DeepSeek Usage, avec détail par élève (AiUsageDaily)."""
    from core.models import AiUsageDaily

    today = date.today()
    days = max(1, min(int(days or 30), 90))
    start = today - timedelta(days=days - 1)
    qs = AiUsageDaily.objects.filter(date__gte=start, date__lte=today)

    today_qs = qs.filter(date=today)
    week_qs = qs.filter(date__gte=today - timedelta(days=6))

    by_feature = []
    for row in (
        qs.values('feature')
        .annotate(
            api_calls=Sum('api_calls'),
            prompt_tokens=Sum('prompt_tokens'),
            completion_tokens=Sum('completion_tokens'),
            cache_hit_tokens=Sum('cache_hit_tokens'),
            cost_usd_micro=Sum('cost_usd_micro'),
        )
        .order_by('-cost_usd_micro')
    ):
        prompt = int(row['prompt_tokens'] or 0)
        completion = int(row['completion_tokens'] or 0)
        cache_hit = int(row['cache_hit_tokens'] or 0)
        by_feature.append({
            'feature': row['feature'],
            'label': _FEATURE_LABELS.get(row['feature'], row['feature']),
            'api_calls': int(row['api_calls'] or 0),
            'prompt_tokens': prompt,
            'completion_tokens': completion,
            'cache_hit_tokens': cache_hit,
            'total_tokens': prompt + completion,
            'cache_hit_pct': round(100.0 * cache_hit / prompt, 1) if prompt else 0.0,
            'cost_usd': _usd(estimate_cost_usd_micro(prompt, completion, cache_hit)),
        })

    top_users = []
    for row in (
        qs.filter(user__isnull=False)
        .values('user_id', 'user__username', 'user__first_name', 'user__last_name', 'user__email')
        .annotate(
            api_calls=Sum('api_calls'),
            prompt_tokens=Sum('prompt_tokens'),
            completion_tokens=Sum('completion_tokens'),
            cache_hit_tokens=Sum('cache_hit_tokens'),
            cost_usd_micro=Sum('cost_usd_micro'),
        )
        .order_by('-cost_usd_micro')[:25]
    ):
        prompt = int(row['prompt_tokens'] or 0)
        completion = int(row['completion_tokens'] or 0)
        cache_hit = int(row['cache_hit_tokens'] or 0)
        name = f"{row.get('user__first_name') or ''} {row.get('user__last_name') or ''}".strip()
        top_users.append({
            'user_id': row['user_id'],
            'username': row.get('user__username') or '',
            'name': name or (row.get('user__username') or f"#{row['user_id']}"),
            'email': row.get('user__email') or '',
            'api_calls': int(row['api_calls'] or 0),
            'prompt_tokens': prompt,
            'completion_tokens': completion,
            'cache_hit_tokens': cache_hit,
            'total_tokens': prompt + completion,
            'cache_hit_pct': round(100.0 * cache_hit / prompt, 1) if prompt else 0.0,
            'cost_usd': _usd(estimate_cost_usd_micro(prompt, completion, cache_hit)),
        })

    top_guests = []
    for row in (
        qs.filter(user__isnull=True)
        .exclude(guest_key='')
        .values('guest_key')
        .annotate(
            api_calls=Sum('api_calls'),
            prompt_tokens=Sum('prompt_tokens'),
            completion_tokens=Sum('completion_tokens'),
            cache_hit_tokens=Sum('cache_hit_tokens'),
            cost_usd_micro=Sum('cost_usd_micro'),
        )
        .order_by('-cost_usd_micro')[:10]
    ):
        prompt = int(row['prompt_tokens'] or 0)
        completion = int(row['completion_tokens'] or 0)
        cache_hit = int(row['cache_hit_tokens'] or 0)
        gk = row.get('guest_key') or ''
        top_guests.append({
            'guest_key': gk[:16],
            'api_calls': int(row['api_calls'] or 0),
            'total_tokens': prompt + completion,
            'cache_hit_pct': round(100.0 * cache_hit / prompt, 1) if prompt else 0.0,
            'cost_usd': _usd(estimate_cost_usd_micro(prompt, completion, cache_hit)),
        })

    daily_map = {
        row['date']: row
        for row in qs.values('date').annotate(
            api_calls=Sum('api_calls'),
            prompt_tokens=Sum('prompt_tokens'),
            completion_tokens=Sum('completion_tokens'),
            cache_hit_tokens=Sum('cache_hit_tokens'),
            cost_usd_micro=Sum('cost_usd_micro'),
        )
    }
    daily = []
    cursor = start
    while cursor <= today:
        row = daily_map.get(cursor)
        if row:
            prompt = int(row['prompt_tokens'] or 0)
            completion = int(row['completion_tokens'] or 0)
            cache_hit = int(row['cache_hit_tokens'] or 0)
            daily.append({
                'date': cursor.isoformat(),
                'label': cursor.strftime('%d/%m'),
                'api_calls': int(row['api_calls'] or 0),
                'prompt_tokens': prompt,
                'completion_tokens': completion,
                'total_tokens': prompt + completion,
                'cache_hit_tokens': cache_hit,
                'cost_usd': _usd(estimate_cost_usd_micro(prompt, completion, cache_hit)),
            })
        else:
            daily.append({
                'date': cursor.isoformat(),
                'label': cursor.strftime('%d/%m'),
                'api_calls': 0,
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'total_tokens': 0,
                'cache_hit_tokens': 0,
                'cost_usd': 0.0,
            })
        cursor += timedelta(days=1)

    today_stats = _serialize_period(today_qs)
    return {
        'generated_at': datetime.now(dt_timezone.utc).isoformat(),
        'delay_notice': (
            'Les totaux Outoubon sont presque temps réel (chaque appel API). '
            'Le solde DeepSeek peut avoir jusqu’à 5 minutes de retard.'
        ),
        'prices': {
            'input_per_m': _PRICE_INPUT_PER_M,
            'output_per_m': _PRICE_OUTPUT_PER_M,
            'cache_hit_per_m': _PRICE_CACHE_HIT_PER_M,
            'model': 'deepseek-v4-flash',
        },
        'balance': fetch_deepseek_balance(),
        'today': today_stats,
        'last_7d': _serialize_period(week_qs),
        'period': {
            **_serialize_period(qs),
            'days': days,
            'start': start.isoformat(),
            'end': today.isoformat(),
        },
        'by_feature': by_feature,
        'top_users': top_users,
        'top_guests': top_guests,
        'daily': daily,
        'limits': {
            'max_global_tokens_per_day': MAX_GLOBAL_TOKENS_PER_DAY,
            'max_global_calls_per_day': MAX_GLOBAL_API_CALLS_PER_DAY,
            'tokens_used_today': today_stats['total_tokens'],
            'calls_used_today': today_stats['api_calls'],
        },
        'global_cache_today': get_global_usage(),
    }


def _db_global_usage_today() -> tuple[int, int]:
    """Compteurs persistants (survit aux redémarrages, contrairement au cache mémoire)."""
    try:
        from core.models import AiUsageDaily
        agg = AiUsageDaily.objects.filter(date=date.today()).aggregate(
            api_calls=Sum('api_calls'),
            prompt_tokens=Sum('prompt_tokens'),
            completion_tokens=Sum('completion_tokens'),
        )
        calls = int(agg['api_calls'] or 0)
        tokens = int(agg['prompt_tokens'] or 0) + int(agg['completion_tokens'] or 0)
        return calls, tokens
    except Exception as exc:
        logger.warning('AiUsageDaily global read failed: %s', exc)
        return 0, 0


def assert_api_budget(model: str = '') -> None:
    """Lève AiBudgetExceeded si un plafond est atteint (avant l'appel API)."""
    db_calls, db_tokens = _db_global_usage_today()
    today_calls = max(int(cache.get(_global_calls_key(), 0) or 0), db_calls)
    today_tokens = max(int(cache.get(_global_tokens_key(), 0) or 0), db_tokens)
    if today_calls >= MAX_GLOBAL_API_CALLS_PER_DAY:
        logger.warning('Global AI call limit reached: %s/%s', today_calls, MAX_GLOBAL_API_CALLS_PER_DAY)
        raise AiBudgetExceeded('global_calls')
    if today_tokens >= MAX_GLOBAL_TOKENS_PER_DAY:
        logger.warning('Global AI token limit reached: %s/%s', today_tokens, MAX_GLOBAL_TOKENS_PER_DAY)
        raise AiBudgetExceeded('global_tokens')

    user = get_ai_context_user()
    if user is not None and getattr(user, 'is_authenticated', False):
        from core.premium import can_make_ai_request
        if not can_make_ai_request(user)[0]:
            raise AiBudgetExceeded('user_daily')
        return

    guest_key = get_ai_context_guest_key()
    if guest_key:
        guest_calls = int(cache.get(_guest_calls_key(guest_key), 0) or 0)
        if guest_calls >= MAX_GUEST_AI_API_CALLS_PER_DAY:
            raise AiBudgetExceeded('guest_daily')


def _record_daily_usage(
    *,
    user,
    guest_key: str,
    feature: str,
    prompt_t: int,
    completion_t: int,
    cache_hit: int,
    cost_micro: int,
) -> None:
    """Upsert agrégat journalier (best-effort, ne bloque pas l'appel IA)."""
    try:
        from core.models import AiUsageDaily

        today = date.today()
        lookup = {
            'user': user if user and getattr(user, 'is_authenticated', False) else None,
            'guest_key': '' if user and getattr(user, 'is_authenticated', False) else (guest_key or ''),
            'date': today,
            'feature': feature if feature in dict(AiUsageDaily.FEATURE_CHOICES) else 'other',
        }
        row, created = AiUsageDaily.objects.get_or_create(
            **lookup,
            defaults={
                'api_calls': 1,
                'prompt_tokens': prompt_t,
                'completion_tokens': completion_t,
                'cache_hit_tokens': cache_hit,
                'cost_usd_micro': cost_micro,
            },
        )
        if not created:
            AiUsageDaily.objects.filter(pk=row.pk).update(
                api_calls=F('api_calls') + 1,
                prompt_tokens=F('prompt_tokens') + prompt_t,
                completion_tokens=F('completion_tokens') + completion_t,
                cache_hit_tokens=F('cache_hit_tokens') + cache_hit,
                cost_usd_micro=F('cost_usd_micro') + cost_micro,
            )
    except Exception as exc:
        logger.warning('AiUsageDaily upsert failed: %s', exc)


def _micro_log_call(
    *,
    user,
    guest_key: str,
    feature: str,
    model: str,
    prompt_t: int,
    completion_t: int,
    cache_hit: int,
    cost_micro: int,
) -> None:
    if not AI_MICROLOG_ENABLED:
        return
    uid = user.pk if user and getattr(user, 'is_authenticated', False) else None
    logger.info(
        '[AI_MICRO] user_id=%s guest=%s feature=%s model=%s in=%s out=%s cache_hit=%s cost_usd=%.6f',
        uid,
        (guest_key or '')[:12] or '-',
        feature,
        model or '?',
        prompt_t,
        completion_t,
        cache_hit,
        cost_micro / 1_000_000,
    )


def record_api_usage(resp, model: str = '') -> None:
    """Enregistre un appel API réussi (compteurs global + user/guest + micro-log)."""
    prompt_t = completion_t = 0
    cache_hit = 0
    usage = getattr(resp, 'usage', None)
    if usage is not None:
        prompt_t = int(getattr(usage, 'prompt_tokens', 0) or 0)
        completion_t = int(getattr(usage, 'completion_tokens', 0) or 0)
        cache_hit = int(getattr(usage, 'prompt_cache_hit_tokens', 0) or 0)
        details = getattr(usage, 'prompt_tokens_details', None)
        if details is not None:
            cache_hit = cache_hit or int(getattr(details, 'cached_tokens', 0) or 0)
        completion_details = getattr(usage, 'completion_tokens_details', None)
        reasoning_t = 0
        if completion_details is not None:
            reasoning_t = int(getattr(completion_details, 'reasoning_tokens', 0) or 0)
        if reasoning_t or (prompt_t + completion_t) > 20_000:
            logger.warning(
                '[AI_COST] model=%s feature=%s prompt=%s completion=%s reasoning=%s total=%s',
                model or '?',
                get_ai_context_feature(),
                prompt_t,
                completion_t,
                reasoning_t,
                prompt_t + completion_t,
            )
    tokens = prompt_t + completion_t
    cost_micro = estimate_cost_usd_micro(prompt_t, completion_t, cache_hit)
    feature = get_ai_context_feature()

    calls_key = _global_calls_key()
    tokens_key = _global_tokens_key()
    cache.set(calls_key, int(cache.get(calls_key, 0) or 0) + 1, timeout=_CACHE_TTL)
    if tokens:
        cache.set(tokens_key, int(cache.get(tokens_key, 0) or 0) + tokens, timeout=_CACHE_TTL)

    user = get_ai_context_user()
    guest_key = get_ai_context_guest_key() or ''

    _record_daily_usage(
        user=user,
        guest_key=guest_key,
        feature=feature,
        prompt_t=prompt_t,
        completion_t=completion_t,
        cache_hit=cache_hit,
        cost_micro=cost_micro,
    )
    _micro_log_call(
        user=user,
        guest_key=guest_key,
        feature=feature,
        model=model,
        prompt_t=prompt_t,
        completion_t=completion_t,
        cache_hit=cache_hit,
        cost_micro=cost_micro,
    )

    if user is not None and getattr(user, 'is_authenticated', False):
        from core.premium import increment_ai_request
        increment_ai_request(user)
        return

    if guest_key:
        gkey = _guest_calls_key(guest_key)
        cache.set(gkey, int(cache.get(gkey, 0) or 0) + 1, timeout=_CACHE_TTL)


def log_ai_call_detail(resp, model: str = '', messages: list | None = None) -> None:
    """
    Log structuré pour valider les budgets tokens (dashboard / grep logs).
    Activé si DEBUG ou AI_USAGE_LOG=True dans settings/.env.
    """
    if not getattr(settings, 'DEBUG', False) and not getattr(settings, 'AI_USAGE_LOG', False):
        return
    usage = getattr(resp, 'usage', None)
    prompt_t = int(getattr(usage, 'prompt_tokens', 0) or 0) if usage else 0
    completion_t = int(getattr(usage, 'completion_tokens', 0) or 0) if usage else 0
    cache_hit = cache_miss = 0
    if usage is not None:
        cache_hit = int(getattr(usage, 'prompt_cache_hit_tokens', 0) or 0)
        cache_miss = int(getattr(usage, 'prompt_cache_miss_tokens', 0) or 0)
        details = getattr(usage, 'prompt_tokens_details', None)
        if details is not None:
            cache_hit = cache_hit or int(getattr(details, 'cached_tokens', 0) or 0)
    est_chars = 0
    if messages:
        for m in messages:
            c = m.get('content')
            if isinstance(c, str):
                est_chars += len(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get('type') == 'text':
                        est_chars += len(str(part.get('text', '')))
    logger.info(
        '[AI_USAGE] model=%s prompt_tokens=%s completion_tokens=%s total=%s '
        'prompt_cache_hit_tokens=%s prompt_cache_miss_tokens=%s est_input_chars=%s feature=%s',
        model or '?',
        prompt_t,
        completion_t,
        prompt_t + completion_t,
        cache_hit,
        cache_miss,
        est_chars,
        get_ai_context_feature(),
    )


def try_acquire_quiz_seed_lock(subject: str, ttl: int = 3600) -> bool:
    """Un seul seeding quiz par matière et par jour."""
    lock_key = f'quiz_seed_lock_{subject}_{_today_key()}'
    return cache.add(lock_key, 1, timeout=ttl)


def budget_exceeded_json(reason: str = 'budget') -> dict:
    messages = {
        'user_daily': 'Limite journalière atteinte. Réessaie demain ou passe au Premium.',
        'guest_daily': 'Limite démo atteinte. Crée un compte gratuit pour continuer.',
        'global_calls': 'Service IA temporairement saturé. Réessaie dans quelques minutes.',
        'global_tokens': 'Service IA temporairement saturé. Réessaie dans quelques minutes.',
    }
    from core.premium import get_reset_time
    msg = messages.get(reason, 'Limite IA atteinte.')
    if reason == 'user_daily':
        msg = f'Limite atteinte. Réessaie dans {get_reset_time()}.'
    return {
        'error': 'daily_limit_reached' if reason in ('user_daily', 'guest_daily') else 'ai_budget_exceeded',
        'reason': reason,
        'message': msg,
        'upgrade_url': '/signup/' if reason == 'guest_daily' else '/pricing/',
    }
