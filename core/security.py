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


# ── Paths and their limits ──
# (prefix, max_requests, window_seconds)
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
            if path.startswith(prefix):
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
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com https://openfpcdn.io https://www.googletagmanager.com https://www.google-analytics.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
                "font-src 'self' data: https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.gstatic.com https://use.fontawesome.com https://ka-f.fontawesome.com; "
                "img-src 'self' data: blob: https://res.cloudinary.com https://*.googleusercontent.com; "
                "connect-src 'self' https://cdnjs.cloudflare.com https://openfpcdn.io https://api.groq.com https://generativelanguage.googleapis.com; "
                "frame-src 'none'; "
                "object-src 'none'; "
                "base-uri 'self';"
            )
        return response
