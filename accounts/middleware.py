import hashlib
from datetime import timedelta
from django.contrib.auth import logout
from django.utils import timezone


class SingleDeviceMiddleware:
    """Force single-device login with 5-minute grace period.
    When a new device logs in, old device gets 5 minutes before disconnection.
    Device changes are locked for 15 days after each switch.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.session.session_key:
            # Exempt account: no device restriction — can login from any device
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
                        # During 5-min grace, let both devices work
                    else:
                        # Unknown session (not the pending device) → logout
                        logout(request)

        return self.get_response(request)


class VisitorTrackingMiddleware:
    """Track page visits for admin analytics. Only tracks page loads, not API calls.
    Rules:
    - 1 visit per IP per day (deduplication)
    - Admin panel users (session _otb_admin_ok) are never tracked
    - Authenticated admin/staff users are never tracked
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only track GET requests for pages (not API/static)
        if (request.method == 'GET'
                and not request.path.startswith('/api/')
                and not request.path.startswith('/static/')
                and not request.path.startswith('/media/')
                and not request.path.startswith('/dashboard/otb-ctrl-9x7k/')
                and 'text/html' in response.get('Content-Type', '')):
            # Skip admin panel sessions and staff/superusers
            if request.session.get('_otb_admin_ok'):
                return response
            if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
                return response
            try:
                from django.utils import timezone as _tz
                from accounts.models import SiteVisit
                import uuid

                today_str = _tz.now().date().isoformat()
                visited_today = request.get_signed_cookie('otb_visitor', default=None)

                # 1 visit per device per day — skip if already recorded today (cookie exists)
                if visited_today != today_str:
                    dummy_hash = str(uuid.uuid4()).replace('-', '')[:32]
                    SiteVisit.objects.create(
                        ip_hash=dummy_hash,
                        path=request.path[:500],
                        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                        user=request.user if request.user.is_authenticated else None,
                    )
                    response.set_signed_cookie('otb_visitor', today_str, max_age=86400)
            except Exception:
                pass  # Never break the response for tracking

        return response
