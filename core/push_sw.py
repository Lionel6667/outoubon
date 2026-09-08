"""Service worker FCM — doit être servi à /firebase-messaging-sw.js."""
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET


def _cfg():
    return getattr(settings, 'FIREBASE_WEB_CONFIG', {}) or {}


@require_GET
@csrf_exempt
def firebase_messaging_sw(request):
    c = _cfg()
    body = f"""/* OU TOU BON — Firebase Messaging SW */
importScripts('https://www.gstatic.com/firebasejs/10.13.2/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.13.2/firebase-messaging-compat.js');
firebase.initializeApp({{
  apiKey: {c.get('apiKey', '')!r},
  authDomain: {c.get('authDomain', '')!r},
  projectId: {c.get('projectId', '')!r},
  storageBucket: {c.get('storageBucket', '')!r},
  messagingSenderId: {c.get('messagingSenderId', '')!r},
  appId: {c.get('appId', '')!r}
}});
const messaging = firebase.messaging();
messaging.onBackgroundMessage(function(payload) {{
  const d = (payload && payload.data) || {{}};
  const title = d.title || (payload.notification && payload.notification.title) || 'OU TOU BON';
  const body = d.body || (payload.notification && payload.notification.body) || '';
  const url = d.url || '/dashboard/';
  return self.registration.showNotification(title, {{
    body: body,
    icon: '/static/img/logo.png',
    badge: '/static/img/logo.png',
    data: {{ url: url, kind: d.kind || '' }}
  }});
}});
self.addEventListener('notificationclick', function(event) {{
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/dashboard/';
  event.waitUntil(clients.matchAll({{ type: 'window', includeUncontrolled: true }}).then(function(list) {{
    for (const c of list) {{
      if (c.url && 'focus' in c) {{
        c.navigate(url);
        return c.focus();
      }}
    }}
    if (clients.openWindow) return clients.openWindow(url);
  }}));
}});
"""
    resp = HttpResponse(body, content_type='application/javascript; charset=utf-8')
    resp['Service-Worker-Allowed'] = '/'
    resp['Cache-Control'] = 'no-cache'
    return resp
