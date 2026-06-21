"""
Security middleware: rate limiting and extra HTTP headers.
"""
import time
import logging
from collections import defaultdict
from threading import Lock

from django.http import JsonResponse

logger = logging.getLogger(__name__)


# ── In-memory rate limiter (thread-safe) ──
class _RateBucket:
    """Sliding-window counter per key."""
    __slots__ = ('hits', 'window_start')

    def __init__(self):
        self.hits = 0
        self.window_start = 0.0


_buckets: dict[str, _RateBucket] = defaultdict(_RateBucket)
_lock = Lock()


def _is_rate_limited(key: str, max_hits: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _lock:
        b = _buckets[key]
        if now - b.window_start > window_seconds:
            b.hits = 1
            b.window_start = now
            return False
        b.hits += 1
        return b.hits > max_hits


def _client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _path_matches(path: str, fragment: str) -> bool:
    return fragment in path


def _path_consumes_ai(path: str) -> bool:
    """True si l'endpoint déclenche un appel IA (DeepSeek/Gemini)."""
    path = path.lower()
    if _path_matches(path, '/api/chat/session/') or _path_matches(path, '/api/chat/suggestions/'):
        return False
    if path.rstrip('/').endswith('/api/chat'):
        return True
    ai_fragments = (
        '/api/solve/',
        '/api/quiz/questions/',
        '/api/quiz/analyse/',
        '/api/extra-bet/ai-help/',
        '/api/exercices/get/',
        '/api/exercices/analyze/',
        '/api/exercices/correct/',
        '/api/exercices/teach/',
        '/api/exercices/similar/',
        '/api/exercices/chat/',
        '/api/exam/ai-correct/',
        '/api/fiches/generate/',
        '/api/plan/generate/',
        '/api/coaching',
        '/api/smart-coach/',
        '/api/cours/chat/',
        '/api/cours/section/',
        '/api/cours/miniquiz/',
        '/api/course-question/',
        '/api/chapter-summary/',
        '/api/generate-exercises/',
        '/api/cours/physique/section/',
        '/api/cours/physique/miniquiz/',
        '/api/cours/physique/exercises/',
        '/api/cours/sc-social/correct/',
        '/api/examen-blanc/generate',
        '/api/translate/',
    )
    return any(_path_matches(path, frag) for frag in ai_fragments)


# Endpoints qui incrémentent déjà ai_request_count via increment_chat/quiz/exercise
_AI_SELF_COUNTED_FRAGMENTS = (
    '/api/quiz/questions/',
    '/api/exercices/get/',
)


def _ai_counted_by_view(path: str) -> bool:
    path = path.lower()
    if path.rstrip('/').endswith('/api/chat'):
        return True
    return any(_path_matches(path, frag) for frag in _AI_SELF_COUNTED_FRAGMENTS)


# ── Paths and their limits ──
# (substring in path, max_requests, window_seconds)
_RATE_LIMITS = [
    ('/api/login',             5,  60),     # 5 login attempts / min
    ('/api/agent-login',       5,  60),
    ('/api/agent-register',    3,  60),     # 3 registrations / min
    ('/api/verify-token',      5,  60),
    ('/api/signup',            5,  60),
    ('/api/send-otp',          3,  120),    # 3 OTP / 2 min
    ('/api/chat',             30,  60),     # 30 AI calls / min
    ('/api/solve',            20,  60),
    ('/api/generate',         15,  60),
    ('/api/quiz',             20,  60),
]


class RateLimitMiddleware:
    """Simple IP-based rate limiter for sensitive endpoints."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path.lower()
        for prefix, max_hits, window in _RATE_LIMITS:
            if _path_matches(path, prefix):
                ip = _client_ip(request)
                key = f"{prefix}:{ip}"
                if _is_rate_limited(key, max_hits, window):
                    logger.warning("Rate limited: %s on %s", ip, prefix)
                    return JsonResponse(
                        {'error': 'Trop de requêtes. Réessayez dans quelques instants.'},
                        status=429,
                    )
                break
        return self.get_response(request)


class UserDailyAiLimitMiddleware:
    """Plafond journalier de requêtes IA par utilisateur (50/jour, tous plans)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path.lower()
        consumes_ai = _path_consumes_ai(path)
        user = getattr(request, 'user', None)

        if consumes_ai and user and user.is_authenticated:
            from core.premium import can_make_ai_request, increment_ai_request, daily_limit_reached_json
            allowed, _ = can_make_ai_request(user)
            if not allowed:
                logger.warning("Daily AI limit: user_id=%s path=%s", user.pk, path)
                return JsonResponse(daily_limit_reached_json(), status=429)

        response = self.get_response(request)

        if (
            consumes_ai
            and user
            and user.is_authenticated
            and 200 <= response.status_code < 300
            and not _ai_counted_by_view(path)
        ):
            skip_quota = False
            try:
                import json as _json
                body = getattr(response, 'content', b'') or b''
                if body:
                    payload = _json.loads(body.decode('utf-8'))
                    if payload.get('cached'):
                        skip_quota = True
            except Exception:
                pass
            if not skip_quota:
                from core.premium import increment_ai_request
                try:
                    increment_ai_request(user)
                except Exception:
                    logger.exception("Failed to increment AI usage for user_id=%s", user.pk)

        return response


class SecurityHeadersMiddleware:
    """Add extra security headers that Django doesn't set by default."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        # HSTS — only over HTTPS to avoid breaking local dev
        if request.is_secure():
            response['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
        # CSP: allow inline for now (needed by KaTeX & templates) but block external scripts
        if 'Content-Security-Policy' not in response:
            response['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com https://openfpcdn.io https://www.googletagmanager.com https://www.google-analytics.com https://static.cloudflareinsights.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
                "font-src 'self' data: https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.gstatic.com https://use.fontawesome.com https://ka-f.fontawesome.com; "
                "img-src 'self' data: blob: https://res.cloudinary.com https://*.googleusercontent.com; "
                "connect-src 'self' https://cdnjs.cloudflare.com https://openfpcdn.io https://api.groq.com https://generativelanguage.googleapis.com https://cloudflareinsights.com; "
                "frame-src 'none'; "
                "object-src 'none'; "
                "base-uri 'self';"
            )
        return response
