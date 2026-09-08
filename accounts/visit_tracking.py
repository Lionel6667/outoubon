"""Helpers for visitor analytics (unique IP/day, Haiti geo)."""
from __future__ import annotations

import hashlib
from django.conf import settings

HAITI_COUNTRY = 'HT'


def get_client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return (request.META.get('REMOTE_ADDR') or '').strip()


def hash_client_ip(ip: str) -> str:
    salt = (getattr(settings, 'VISITOR_IP_SALT', None) or settings.SECRET_KEY)[:32]
    return hashlib.sha256(f'{salt}:{ip}'.encode()).hexdigest()


def get_country_code(request) -> str:
    cc = (request.META.get('HTTP_CF_IPCOUNTRY') or '').strip().upper()
    if len(cc) == 2 and cc != 'XX':
        return cc
    return ''


def is_haiti_visitor(request) -> bool:
    cc = get_country_code(request)
    if cc == HAITI_COUNTRY:
        return True
  # Dev / sans géo Cloudflare : ne pas compter les IP étrangères explicites
    if settings.DEBUG and not cc:
        return True
    return False


def should_track_visit(request) -> bool:
    if request.session.get('_otb_admin_ok'):
        return False
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return False
    return is_haiti_visitor(request)
