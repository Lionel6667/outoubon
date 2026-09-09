(function () {
  const cfg = window.OTB_FIREBASE || null;
  const vapid = window.OTB_VAPID || '';
  // Le navigateur supporte-t-il le push web ? (iOS in-app, vieux navigateurs, HTTP…)
  const SUPPORTED = ('Notification' in window) && ('serviceWorker' in navigator);
  const CONFIGURED = !!(cfg && vapid);
  const csrf = function () { return (typeof CSRF !== 'undefined') ? CSRF : ''; };
  const KEY_LATER = 'otb_push_later';
  const KEY_NAGS = 'otb_push_nags';
  const KEY_VISITS = 'otb_push_visits';

  // Notification visible à l'utilisateur (jamais silencieux : le bouton doit réagir).
  function toast(msg, type) {
    try {
      if (typeof window.showToast === 'function') { window.showToast(msg, type || 'info'); return; }
    } catch (e) {}
    // Repli minimal si showToast n'est pas chargé.
    try { console[(type === 'error') ? 'error' : 'log']('[push] ' + msg); } catch (e) {}
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  async function ensureFirebase() {
    if (window.firebase && window.firebase.messaging) return;
    await loadScript('https://www.gstatic.com/firebasejs/10.13.2/firebase-app-compat.js');
    await loadScript('https://www.gstatic.com/firebasejs/10.13.2/firebase-messaging-compat.js');
    if (!firebase.apps.length) firebase.initializeApp(cfg);
  }

  function skipForeground(kind, url) {
    if (document.visibilityState !== 'visible') return false;
    const path = location.pathname || '';
    if ((kind === 'dm' || kind === 'group_mention' || kind === 'group_reply' || kind === 'group_everyone' || kind === 'admin_annonce') && path.indexOf('/dashboard/amis') === 0) return true;
    if ((kind === 'match_found' || kind === 'duel_joined' || kind === 'duel_result') && path.indexOf('/dashboard/match') === 0) return true;
    if (kind === 'genius' && path.indexOf('/dashboard/genius') === 0) return true;
    if (url && path && url.indexOf(path) !== -1) return true;
    return false;
  }

  async function registerToken() {
    await ensureFirebase();
    const reg = await navigator.serviceWorker.register('/firebase-messaging-sw.js', { scope: '/' });
    const messaging = firebase.messaging();
    const token = await messaging.getToken({ vapidKey: vapid, serviceWorkerRegistration: reg });
    if (!token) throw new Error('no-token');
    const resp = await fetch('/dashboard/api/push/register/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
      body: JSON.stringify({ token: token })
    });
    if (!resp.ok) throw new Error('register-failed');
    try { localStorage.setItem('otb_push_token', token); } catch (e) {}
    messaging.onMessage(function (payload) {
      const d = (payload && payload.data) || {};
      if (skipForeground(d.kind, d.url)) return;
      if (Notification.permission === 'granted') {
        new Notification(d.title || 'OU TOU BON', {
          body: d.body || '',
          icon: '/static/img/logo.png'
        });
      }
    });
    return token;
  }

  // Flux complet déclenché par un clic utilisateur — donne TOUJOURS un retour visible.
  // Exposé globalement pour pouvoir être appelé depuis n'importe quelle page/bouton.
  var _enabling = false;
  async function enablePush(opts) {
    opts = opts || {};
    if (_enabling) return;
    if (!SUPPORTED) {
      toast("Ton navigateur ne supporte pas les notifications. Essaie Chrome (Android) ou installe l'app.", 'error');
      return;
    }
    if (!CONFIGURED) {
      toast('Notifications momentanément indisponibles. Réessaie plus tard.', 'error');
      return;
    }
    if (Notification.permission === 'denied') {
      toast("Les notifications sont bloquées dans ton navigateur. Autorise-les dans les réglages du site (icône 🔒 à côté de l'adresse).", 'error');
      return;
    }
    _enabling = true;
    var go = document.getElementById('otbPushGo');
    var hint = document.getElementById('otbPushHint');
    if (go) go.disabled = true;
    if (hint) { hint.style.display = 'block'; hint.textContent = 'Le navigateur va te demander l’autorisation…'; }
    try {
      var perm = await Notification.requestPermission();
      if (perm === 'granted') {
        if (hint) hint.textContent = 'Activation en cours…';
        await registerToken();
        hideModal();
        toast('Notifications activées ✅', 'success');
      } else if (perm === 'denied') {
        hideModal();
        markLater();
        toast("Tu as refusé les notifications. Tu peux les réactiver dans les réglages du navigateur.", 'info');
      } else {
        // 'default' : l'utilisateur a fermé la demande sans choisir.
        markLater();
        toast('Tu pourras activer les notifications plus tard.', 'info');
      }
    } catch (e) {
      toast("Impossible d'activer les notifications (problème réseau ou navigateur). Réessaie.", 'error');
      try { console.error('[push] enablePush failed', e); } catch (_) {}
    } finally {
      _enabling = false;
      if (go) go.disabled = false;
      var el = document.getElementById('otbPushModal');
      if (el) el.classList.remove('is-asking');
    }
  }
  window.otbEnablePush = enablePush;

  function bumpVisits() {
    try {
      var n = Number(localStorage.getItem(KEY_VISITS) || 0) + 1;
      localStorage.setItem(KEY_VISITS, String(n));
      return n;
    } catch (e) { return 1; }
  }

  function snoozed() {
    try {
      var later = Number(localStorage.getItem(KEY_LATER) || 0);
      var nags = Number(localStorage.getItem(KEY_NAGS) || 0);
      var wait = nags >= 3 ? 36e5 * 24 * 7 : 36e5 * 24;
      return later && (Date.now() - later) < wait;
    } catch (e) { return false; }
  }

  function markLater() {
    try {
      localStorage.setItem(KEY_LATER, String(Date.now()));
      localStorage.setItem(KEY_NAGS, String(Number(localStorage.getItem(KEY_NAGS) || 0) + 1));
    } catch (e) {}
  }

  function isBusyPath() {
    return /\/dashboard\/(quiz|duel|examen-blanc|match|exercices)(\/|$)/.test(location.pathname || '');
  }

  function isGoodPage() {
    var p = location.pathname || '';
    if (/\/dashboard\/amis(\/|$)/.test(p)) return true;
    if (/\/dashboard\/?$/.test(p)) return true;
    if (/\/dashboard\/(profil|progression|gains)(\/|$)/.test(p)) return true;
    return false;
  }

  function blockingModalOpen() {
    if (window.OTB_COACH_NAME_REQUIRED) return true;
    var coach = document.getElementById('coachNameModal');
    if (coach && coach.style.display !== 'none') return true;
    var gpm = document.getElementById('globalUserProfileModal');
    if (gpm && gpm.style.display === 'flex') return true;
    return false;
  }

  function hideModal() {
    var el = document.getElementById('otbPushModal');
    if (el) el.hidden = true;
  }

  // Attache les gestionnaires du modal une seule fois, dès que possible (pas
  // seulement à l'ouverture) — évite toute course « clic avant binding ».
  function bindModalHandlers() {
    var el = document.getElementById('otbPushModal');
    if (!el) return;
    var go = document.getElementById('otbPushGo');
    var later = document.getElementById('otbPushLater');
    var backdrop = el.querySelector('.otb-push-modal__backdrop');
    if (go && !go._bound) {
      go._bound = true;
      go.addEventListener('click', function (ev) {
        ev.preventDefault();
        el.classList.add('is-asking');
        enablePush();
      });
    }
    if (later && !later._bound) {
      later._bound = true;
      later.addEventListener('click', function () { hideModal(); markLater(); });
    }
    if (backdrop && !backdrop._bound) {
      backdrop._bound = true;
      backdrop.addEventListener('click', function () { hideModal(); markLater(); });
    }
  }

  function showModal() {
    var el = document.getElementById('otbPushModal');
    if (!el || !el.hidden) return;
    bindModalHandlers();
    el.hidden = false;
  }

  function maybeAsk() {
    if (Notification.permission !== 'default') return;
    if (snoozed()) return;
    if (blockingModalOpen()) return;
    if (isBusyPath()) return;
    var p = location.pathname || '';
    var onMessages = /\/dashboard\/amis(\/|$)/.test(p);
    var onHome = /\/dashboard\/?$/.test(p);
    if (onMessages || onHome) {
      showModal();
      return;
    }
    var visits = 0;
    try { visits = Number(localStorage.getItem(KEY_VISITS) || 0); } catch (e) {}
    if (visits >= 3 && isGoodPage()) showModal();
  }

  var askInterval = null;
  var tokenReady = false;

  function scheduleAsk() {
    if (askInterval) {
      clearInterval(askInterval);
      askInterval = null;
    }
    if (Notification.permission === 'granted') {
      if (!tokenReady) {
        tokenReady = true;
        registerToken().catch(function () {});
      }
      return;
    }
    if (Notification.permission !== 'default' || snoozed()) return;
    var started = Date.now();
    var tries = 0;
    askInterval = setInterval(function () {
      tries += 1;
      var elapsed = Date.now() - started;
      var p = location.pathname || '';
      var onMessages = /\/dashboard\/amis(\/|$)/.test(p);
      var onHome = /\/dashboard\/?$/.test(p);
      if (blockingModalOpen() || isBusyPath()) return;
      if (onMessages && elapsed > 8000) {
        clearInterval(askInterval);
        askInterval = null;
        maybeAsk();
        return;
      }
      if (onHome && elapsed > 35000) {
        clearInterval(askInterval);
        askInterval = null;
        maybeAsk();
        return;
      }
      if (elapsed > 50000 && isGoodPage()) {
        clearInterval(askInterval);
        askInterval = null;
        maybeAsk();
        return;
      }
      if (tries > 40) {
        clearInterval(askInterval);
        askInterval = null;
      }
    }, 1500);
  }

  document.addEventListener('DOMContentLoaded', function () {
    // Toujours attacher les gestionnaires : le bouton du modal doit réagir
    // même si l'auto-affichage est désactivé sur cette page.
    bindModalHandlers();
    if (!SUPPORTED || !CONFIGURED) return;
    bumpVisits();
    scheduleAsk();
  });

  document.addEventListener('otb:spa-navigate', function () {
    bindModalHandlers();
    if (!SUPPORTED || !CONFIGURED) return;
    scheduleAsk();
  });
})();
