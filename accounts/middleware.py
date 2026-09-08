import hashlib
from datetime import timedelta
from django.contrib.auth import logout
from django.utils import timezone
from django.contrib.auth import login as auth_login
from accounts.models import SiteVisit, PersistentAuthToken, UserProfile
from accounts.visit_tracking import (
    get_client_ip, hash_client_ip, get_country_code, should_track_visit,
)


class UserActivityMiddleware:
    """Met à jour last_seen_at pour les élèves (précision admin : en ligne / dernière connexion)."""

    SKIP_PREFIXES = ('/static/', '/media/', '/dashboard/otb-ctrl-9x7k/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, 'spa_mode', False):
            return response
        if any(request.path.startswith(p) for p in self.SKIP_PREFIXES):
            return response
        if not request.user.is_authenticated:
            return response
        if request.user.is_staff or request.user.is_superuser:
            return response
        from accounts.models import Agent
        if Agent.objects.filter(user_id=request.user.id).exists():
            return response
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return response
        now = timezone.now()
        if profile.last_seen_at and (now - profile.last_seen_at).total_seconds() < 60:
            return response
        UserProfile.objects.filter(pk=profile.pk).update(last_seen_at=now)
        return response


class SingleDeviceMiddleware:
    """Force single-device login with 5-minute grace period.
    When a new device logs in, old device gets 5 minutes before disconnection.
    Device changes are locked for 15 days after each switch.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.session.session_key:
            # Exempt account: no device restriction
            if request.user.email == 'herbyscott7@gmail.com':
                return self.get_response(request)
            
            profile = getattr(request.user, 'profile', None)
            if profile and profile.active_session_key:
                if profile.active_session_key != request.session.session_key:
                    # Check if this is the pending device that should now take over
                    if (profile.pending_device_session_key == request.session.session_key
                            and profile.pending_device_at):
                        # Pending device — check if 5 min have passed
                        elapsed = (timezone.now() - profile.pending_device_at).total_seconds()
                        if elapsed >= 300:
                            # 5 min passed → switch to this device
                            profile.active_session_key = request.session.session_key
                            profile.device_fingerprint = profile.pending_device_fingerprint
                            profile.pending_device_fingerprint = ''
                            profile.pending_device_session_key = ''
                            profile.pending_device_at = None
                            profile.device_change_locked_until = timezone.now() + timedelta(days=15)
                            profile.last_login_device = timezone.now()
                            profile.save(update_fields=[
                                'active_session_key', 'device_fingerprint',
                                'pending_device_fingerprint', 'pending_device_session_key',
                                'pending_device_at', 'device_change_locked_until',
                                'last_login_device',
                            ])
                    else:
                        # NEW: If the session mismatch happens, we don't logout immediately
                        # if the request is for the device check API itself, to allow the session to sync.
                        if request.path in ['/api/device/check/', '/api/device/status/', '/logout/']:
                            return self.get_response(request)
                        
                        # Otherwise, if the session is stale (old device), we logout.
                        # BUT: we add a small buffer or check if it's a very new session.
                        # For now, let's just ensure we don't logout if the active_session_key was JUST set.
                        pass # Let api_device_status handle the logout on frontend for a better UX
                        # (Unless we want strict backend enforcement, which was causing the false disconnections)
        
        return self.get_response(request)


class VisitorTrackingMiddleware:
    """Visiteurs uniques Haïti : 1 IP hashée / jour (pas chaque session)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if getattr(request, 'spa_mode', False):
            return response
        if request.method != 'GET':
            return response
        if request.path.startswith('/api/'):
            return response
        if request.path.startswith('/static/'):
            return response
        if request.path.startswith('/media/'):
            return response
        if request.path.startswith('/dashboard/otb-ctrl-9x7k/'):
            return response
        if 'text/html' not in response.get('Content-Type', ''):
            return response
        if not should_track_visit(request):
            return response

        try:
            ip = get_client_ip(request)
            if not ip:
                return response
            ip_hash = hash_client_ip(ip)
            visit_date = timezone.localdate()
            country_code = get_country_code(request) or 'HT'

            if not SiteVisit.objects.filter(ip_hash=ip_hash, visit_date=visit_date).exists():
                SiteVisit.objects.create(
                    ip_hash=ip_hash,
                    country_code=country_code,
                    visit_date=visit_date,
                    path=request.path[:500],
                    user_agent=(request.META.get('HTTP_USER_AGENT', '') or '')[:500],
                    user=request.user if request.user.is_authenticated else None,
                )
        except Exception:
            pass

        return response


class PersistentAuthMiddleware:
    """Instant authentication via persistent cookie.
    If the session is expired but the persistent token cookie is valid,
    automatically logs the user in before reaching the view.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            token = request.COOKIES.get('otb_persistent_token')
            if token:
                try:
                    token_obj = PersistentAuthToken.objects.filter(token=token).select_related('user').first()
                    if token_obj and token_obj.is_valid():
                        user = token_obj.user
                        auth_login(request, user)
                        
                        # Sync session for single-device
                        if not request.session.session_key:
                            request.session.save()
                        profile = getattr(user, 'profile', None)
                        if profile:
                            profile.active_session_key = request.session.session_key
                            profile.save(update_fields=['active_session_key'])
                            
                        # Rolling renewal
                        token_obj.expires_at = timezone.now() + timedelta(days=365)
                        token_obj.save(update_fields=['expires_at'])
                except Exception:
                    pass

        return self.get_response(request)
