(function () {
  if (!window.OTB_FIREBASE || !window.OTB_VAPID) return;
  if (!('Notification' in window) || !('serviceWorker' in navigator)) return;

  const cfg = window.OTB_FIREBASE;
  const vapid = window.OTB_VAPID;
  const csrf = function () { return (typeof CSRF !== 'undefined') ? CSRF : ''; };
  const KEY_LATER = 'otb_push_later';
  const KEY_NAGS = 'otb_push_nags';
  const KEY_VISITS = 'otb_push_visits';

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
    if (!token) return;
    await fetch('/dashboard/api/push/register/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
      body: JSON.stringify({ token: token })
    });
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
  }

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

  function showModal() {
    var el = document.getElementById('otbPushModal');
    if (!el || !el.hidden) return;
    el.hidden = false;
    var go = document.getElementById('otbPushGo');
    var later = document.getElementById('otbPushLater');
    var hint = document.getElementById('otbPushHint');
    var backdrop = el.querySelector('.otb-push-modal__backdrop');
    if (go && !go._bound) {
      go._bound = true;
      go.addEventListener('click', function () {
        el.classList.add('is-asking');
        go.disabled = true;
        if (hint) hint.textContent = 'Le navigateur va te demander l’autorisation…';
        Notification.requestPermission().then(function (p) {
          hideModal();
          if (p === 'granted') registerToken().catch(function () {});
          else markLater();
        }).catch(function () {
          go.disabled = false;
          el.classList.remove('is-asking');
        });
      });
    }
    if (later && !later._bound) {
      later._bound = true;
      later.addEventListener('click', function () {
        hideModal();
        markLater();
      });
    }
    if (backdrop && !backdrop._bound) {
      backdrop._bound = true;
      backdrop.addEventListener('click', function () {
        hideModal();
        markLater();
      });
    }
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
    bumpVisits();
    scheduleAsk();
  });

  document.addEventListener('otb:spa-navigate', function () {
    scheduleAsk();
  });
})();
