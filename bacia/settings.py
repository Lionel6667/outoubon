import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Security: SECRET_KEY ──
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set! Add a strong random key to your .env file.\n"
        "Generate one with: python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\""
    )

DEBUG = os.getenv('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,outoubon.com,www.outoubon.com').split(',') if h.strip()
]
# Allow ngrok for local testing/previews even if DEBUG=False
ALLOWED_HOSTS += ['.ngrok-free.dev', '.ngrok.io', '127.0.0.1', 'localhost']
ALLOWED_HOSTS = list(set(ALLOWED_HOSTS)) # Remove duplicates

# Local development detection (HTTP localhost / ngrok workflows)
_LOCAL_ONLY_HOSTS = {'localhost', '127.0.0.1', '.ngrok-free.dev', '.ngrok.io', 'outoubon.com', 'www.outoubon.com'}
IS_LOCAL_DEV_HOSTS = bool(ALLOWED_HOSTS) and all(h in _LOCAL_ONLY_HOSTS for h in ALLOWED_HOSTS)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'cloudinary_storage',
    'cloudinary',
    'accounts',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.PersistentAuthMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.security.RateLimitMiddleware',
    'core.security.SecurityHeadersMiddleware',
    'accounts.middleware.SingleDeviceMiddleware',
    'accounts.middleware.VisitorTrackingMiddleware',
]

ROOT_URLCONF = 'bacia.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.user_lang',
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

WSGI_APPLICATION = 'bacia.wsgi.application'

import dj_database_url

# Use dj_database_url to parse the DATABASE_URL environment variable
# If not set, it defaults to the local SQLite database.
DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DATABASE_URL', f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'America/Port-au-Prince'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ── Cloudinary (stockage avatars & uploads) ──
import cloudinary
import cloudinary.uploader
import cloudinary.api

cloudinary.config(
    cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME', 'dlmwh1val'),
    api_key    = os.getenv('CLOUDINARY_API_KEY', ''),
    api_secret = os.getenv('CLOUDINARY_SECRET', ''),
    secure     = True,
)

DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Security flags ──────────────────────────────────────────────────────────
X_FRAME_OPTIONS         = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER  = True   # legacy header for older browsers

# Cookies: secure in production, relaxed in dev
CSRF_COOKIE_SECURE   = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY  = False  # must be False so JS can read csrftoken cookie

# Force relaxed cookie/HTTPS behavior on localhost-only environments.
# This prevents session loss on http://127.0.0.1 where Secure cookies are not sent.
if IS_LOCAL_DEV_HOSTS:
    CSRF_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False

# HSTS (activate in production via env — never force in dev over HTTP)
_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
if _HSTS_SECONDS > 0:
    SECURE_HSTS_SECONDS           = _HSTS_SECONDS
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD            = True

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Google Gemini
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GROQ_API_KEY   = os.getenv('GROQ_API_KEY', '')

# PeyemAPI (MonCash)
PEYEM_API_URL        = 'https://fyxmoljbnionrylsmfoo.supabase.co/functions/v1/bazik-api'
PEYEM_SECRET_KEY     = os.getenv('PEYEM_SECRET_KEY', '')
PEYEM_WEBHOOK_SECRET = os.getenv('PEYEM_WEBHOOK_SECRET', '')

# Dossier des examens PDF (535 fichiers dans BacIA_Django/database/)
COURSE_DB_PATH = os.getenv(
    'COURSE_DB_PATH',
    str(BASE_DIR / 'database')
)

# Fichier cache JSON pour l'index des PDFs (évite de re-parser 535 PDFs à chaque démarrage)
PDF_INDEX_CACHE = os.getenv(
    'PDF_INDEX_CACHE',
    str(BASE_DIR / 'database' / '_pdf_index.json')
)

# ──────────────────────────────────────────────────────
# SECURITY SETTINGS (production)
# ──────────────────────────────────────────────────────

# HTTPS / HSTS
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31_536_000          # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    # Keep local workflows functional when running with DEBUG=False on localhost.
    if IS_LOCAL_DEV_HOSTS:
        SECURE_SSL_REDIRECT = False
        SECURE_HSTS_SECONDS = 0

# Session security
SESSION_COOKIE_SECURE   = not DEBUG
SESSION_COOKIE_HTTPONLY  = True
SESSION_COOKIE_SAMESITE  = 'Lax'
SESSION_COOKIE_AGE       = 31536000            # 1 an

# CSRF security
CSRF_COOKIE_SECURE      = not DEBUG
CSRF_COOKIE_HTTPONLY     = True
CSRF_COOKIE_SAMESITE    = 'Lax'
CSRF_TRUSTED_ORIGINS    = [
    o.strip() for o in os.getenv('CSRF_TRUSTED_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000,https://outoubon.com,https://www.outoubon.com').split(',') if o.strip()
]
# Always trust ngrok origins for testing/preview purposes
CSRF_TRUSTED_ORIGINS += ['https://*.ngrok-free.dev', 'https://*.ngrok.io']
CSRF_TRUSTED_ORIGINS = list(set(CSRF_TRUSTED_ORIGINS)) # Remove duplicates

# Browser security headers
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER  = True
X_FRAME_OPTIONS             = 'DENY'

# Prevent host-header attacks
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# ── Logging ──
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': str(BASE_DIR / 'django_errors.log'),
            'formatter': 'verbose',
            'level': 'WARNING',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'core': {
            'handlers': ['console', 'file'],
            'level': 'INFO' if DEBUG else 'WARNING',
            'propagate': False,
        },
        'accounts': {
            'handlers': ['console', 'file'],
            'level': 'INFO' if DEBUG else 'WARNING',
            'propagate': False,
        },
    },
}
