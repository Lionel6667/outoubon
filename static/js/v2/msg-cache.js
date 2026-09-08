/**
 * Cache RAM + IndexedDB des messages (groupe + DMs).
 * Les anciens messages restent locaux ; le réseau ne ramène que le delta (after_id).
 */
(function () {
  'use strict';

  var DB_NAME = 'otb-msg-cache';
  var DB_VER = 1;
  var STORE = 'threads';
  var META = 'meta';
  var CAP = 120;
  var mem = {};
  var remembered = {};
  var warming = false;
  var dbPromise = null;

  function csrf() {
    try {
      if (typeof window.CSRF === 'string' && window.CSRF) return window.CSRF;
    } catch (e) {}
    var m = document.querySelector('meta[name="csrf-token"]');
    return (m && m.content) || '';
  }

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise(function (resolve) {
      if (!window.indexedDB) { resolve(null); return; }
      try {
        var req = indexedDB.open(DB_NAME, DB_VER);
        req.onupgradeneeded = function () {
          var db = req.result;
          if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'key' });
          if (!db.objectStoreNames.contains(META)) db.createObjectStore(META, { keyPath: 'key' });
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { resolve(null); };
      } catch (e) { resolve(null); }
    });
    return dbPromise;
  }

  function idbGet(store, key) {
    return openDb().then(function (db) {
      if (!db) return null;
      return new Promise(function (resolve) {
        try {
          var tx = db.transaction(store, 'readonly');
          var r = tx.objectStore(store).get(key);
          r.onsuccess = function () { resolve(r.result || null); };
          r.onerror = function () { resolve(null); };
        } catch (e) { resolve(null); }
      });
    });
  }

  function idbPut(store, value) {
    return openDb().then(function (db) {
      if (!db) return;
      return new Promise(function (resolve) {
        try {
          var tx = db.transaction(store, 'readwrite');
          tx.objectStore(store).put(value);
          tx.oncomplete = function () { resolve(); };
          tx.onerror = function () { resolve(); };
        } catch (e) { resolve(); }
      });
    });
  }

  function cap(msgs) {
    if (!Array.isArray(msgs)) return [];
    if (msgs.length <= CAP) return msgs;
    return msgs.slice(msgs.length - CAP);
  }

  function byIdMap(msgs) {
    var map = {};
    (msgs || []).forEach(function (m) { if (m && m.id != null) map[m.id] = m; });
    return map;
  }

  function sortMsgs(msgs) {
    return (msgs || []).slice().sort(function (a, b) { return Number(a.id) - Number(b.id); });
  }

  function mergeLists(existing, incoming, deletedForMe, deletedForAll) {
    var map = byIdMap(existing);
    (incoming || []).forEach(function (m) {
      if (!m || m.id == null) return;
      map[m.id] = m;
    });
    (deletedForAll || []).forEach(function (id) {
      var m = map[id];
      if (!m) return;
      m.is_deleted = true;
      m.content = 'Message supprimé pour tous';
      m.image_url = null;
      m.video_url = null;
      m.quiz_data = null;
    });
    (deletedForMe || []).forEach(function (id) { delete map[id]; });
    return cap(sortMsgs(Object.keys(map).map(function (k) { return map[k]; })));
  }

  async function get(key) {
    if (mem[key]) return mem[key].slice();
    var row = await idbGet(STORE, key);
    var msgs = row && Array.isArray(row.messages) ? row.messages : [];
    mem[key] = msgs.slice();
    return msgs.slice();
  }

  async function save(key, messages) {
    var msgs = cap(sortMsgs(messages || []));
    mem[key] = msgs.slice();
    remembered[key] = 1;
    await idbPut(STORE, { key: key, messages: msgs, ts: Date.now() });
    persistRemembered();
    return msgs;
  }

  async function merge(key, incoming, deletedForMe, deletedForAll) {
    var current = await get(key);
    var next = mergeLists(current, incoming, deletedForMe, deletedForAll);
    return save(key, next);
  }

  function lastId(messages) {
    if (!messages || !messages.length) return 0;
    return Number(messages[messages.length - 1].id) || 0;
  }

  function persistRemembered() {
    var keys = Object.keys(remembered);
    idbPut(META, { key: 'threads', keys: keys, ts: Date.now() });
  }

  async function loadRemembered() {
    var row = await idbGet(META, 'threads');
    (row && row.keys || []).forEach(function (k) { remembered[k] = 1; });
    remembered.group = 1;
  }

  function remember(key) {
    if (!key) return;
    remembered[key] = 1;
    persistRemembered();
  }

  function fetchJson(url) {
    return fetch(url, {
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf(), 'X-OTB-SPA': '1' }
    }).then(function (r) { return r.json(); }).catch(function () { return null; });
  }

  async function syncThread(key, url) {
    var cached = await get(key);
    var after = lastId(cached);
    var sep = url.indexOf('?') >= 0 ? '&' : '?';
    var data = await fetchJson(url + sep + 'after_id=' + after);
    if (!data || !data.ok) return cached;
    if (data.incremental) {
      return merge(key, data.messages || [], data.deleted_for_me || [], data.deleted_for_all || []);
    }
    return save(key, data.messages || []);
  }

  async function warmup() {
    if (warming) return;
    if (document.hidden) return;
    warming = true;
    try {
      await loadRemembered();
      remember('group');
      await syncThread('group', '/dashboard/api/group-chat/history/');
      var keys = Object.keys(remembered);
      for (var i = 0; i < keys.length; i++) {
        if (document.hidden) break;
        var k = keys[i];
        if (k === 'group') continue;
        if (k.indexOf('dm:') === 0) {
          var fid = k.slice(3);
          if (!/^\d+$/.test(fid)) continue;
          await syncThread(k, '/dashboard/api/amis/messages/' + fid + '/');
        }
      }
    } catch (e) {
    } finally {
      warming = false;
    }
  }

  window.OTBMsgCache = {
    get: get,
    save: save,
    merge: merge,
    lastId: lastId,
    remember: remember,
    syncThread: syncThread,
    warmup: warmup,
    threadKey: function (mode, friendId) {
      if (mode === 'group') return friendId && friendId !== 'group' ? ('group:' + friendId) : 'group';
      if (mode === 'admin') return 'admin';
      return 'dm:' + friendId;
    }
  };
})();
