/**
 * OU TOU BON V2 — Instant in-app navigation (dashboard shell).
 * Preloads lightweight SPA HTML, swaps DOM from memory (no wait on click).
 */
(function () {
  'use strict';

  var BOTTOM_NAV_PATHS = {
    accueil: '/dashboard/',
    apprendre: '/dashboard/cours/',
    match: '/dashboard/match/',
    astra: '/dashboard/chat/',
    messages: '/dashboard/amis/'
  };

  var HUB_PATHS = {
    accueil: ['/dashboard/', '/dashboard'],
    apprendre: ['/dashboard/cours/', '/dashboard/exercices/', '/dashboard/extra-bet/', '/dashboard/fiches/', '/dashboard/library/'],
    match: ['/dashboard/match/', '/dashboard/quiz/', '/dashboard/examen-blanc/', '/dashboard/genius/'],
    astra: ['/dashboard/chat/', '/dashboard/historique/'],
    messages: ['/dashboard/amis/'],
    profil: ['/dashboard/profil/', '/dashboard/gains/', '/dashboard/progression/', '/dashboard/plan/', '/dashboard/bookmarks/', '/pricing/']
  };

  var SPA_PREFETCH_URLS = [
    '/dashboard/',
    '/dashboard/cours/',
    '/dashboard/exercices/',
    '/dashboard/match/',
    '/dashboard/quiz/',
    '/dashboard/chat/',
    '/dashboard/amis/',
    '/dashboard/genius/',
    '/dashboard/profil/',
    '/dashboard/gains/',
    '/dashboard/fiches/',
    '/dashboard/extra-bet/',
    '/dashboard/library/',
    '/dashboard/plan/',
    '/dashboard/bookmarks/',
    '/dashboard/progression/',
    '/dashboard/historique/',
    '/dashboard/examen-blanc/'
  ];

  var SPA_CORE_V2_CSS = [
    'design-system.css',
    'layout.css',
    'components.css',
    'page-shell.css'
  ];

  var SPA_BODY_CLASSES = [
    'v2-exo-session-active',
    'sidebar-open',
    'exam-zen',
    'mau-open',
    'otb-info-open'
  ];

  var PERMANENT_SCRIPT_MARKERS = [
    'app.js',
    'msg-cache.js',
    'gamification.js',
    'mathjax',
    'purify.min.js',
    'dompurify'
  ];

  var PERMANENT_SCRIPT_IDS = ['MathJax-script'];

  var SPA_DASHBOARD_PREFIX = '/dashboard/';

  var WARM_PRIORITY = [
    '/dashboard/',
    '/dashboard/cours/',
    '/dashboard/match/',
    '/dashboard/chat/',
    '/dashboard/amis/'
  ];

  var MAX_CACHE = 12;

  var _pageCache = {};
  var _pagePackages = {};
  var _cacheOrder = [];
  var _warming = {};
  var _fetchInflight = {};
  var _navAbort = null;
  var _navGen = 0;
  var _navInProgress = false;
  var _navFetchPath = null;
  var _loadedScriptSrcs = {};
  var _navLoadingLink = null;

  document.querySelectorAll('script[src]').forEach(function (s) {
    if (s.src) _loadedScriptSrcs[s.src] = true;
  });

  function normalizePath(path) {
    if (!path) return '/';
    var p = String(path).toLowerCase();
    if (!p.endsWith('/')) p += '/';
    return p;
  }

  window.OTB_clearSpaPageCache = function (pathOrUrl) {
    if (!pathOrUrl) {
      _pageCache = {};
      _pagePackages = {};
      _cacheOrder = [];
      _warming = {};
      return;
    }
    var path = normalizePath(pathOrUrl);
    delete _pageCache[path];
    delete _pagePackages[path];
    delete _warming[path];
    var idx = _cacheOrder.indexOf(path);
    if (idx >= 0) _cacheOrder.splice(idx, 1);
  };

  window.OTB_navigateTo = navigateTo;
  window.OTB_warmSpaPage = warmPage;

  function detectHub(pathname) {
    var path = normalizePath(pathname);
    if (path === '/dashboard/' || path === '/dashboard') return 'accueil';
    var keys = ['apprendre', 'match', 'astra', 'messages', 'profil'];
    for (var i = 0; i < keys.length; i++) {
      var hub = keys[i];
      var list = HUB_PATHS[hub] || [];
      for (var j = 0; j < list.length; j++) {
        var prefix = normalizePath(list[j]);
        if (path === prefix || path.indexOf(prefix) === 0) return hub;
      }
    }
    return null;
  }

  function detectBottomNavHub(pathname) {
    var hub = detectHub(pathname);
    if (hub && BOTTOM_NAV_PATHS[hub]) return hub;
    return null;
  }

  function isSpaPageStylesheet(href) {
    if (!href || href.indexOf('/static/css/v2/') === -1) return false;
    for (var i = 0; i < SPA_CORE_V2_CSS.length; i++) {
      if (href.indexOf(SPA_CORE_V2_CSS[i]) !== -1) return false;
    }
    return true;
  }

  function isPermanentScript(scriptEl) {
    if (!scriptEl) return true;
    if (scriptEl.id && PERMANENT_SCRIPT_IDS.indexOf(scriptEl.id) !== -1) return true;
    var src = scriptEl.getAttribute('src') || '';
    for (var i = 0; i < PERMANENT_SCRIPT_MARKERS.length; i++) {
      if (src.indexOf(PERMANENT_SCRIPT_MARKERS[i]) !== -1) return true;
    }
    return false;
  }

  function isSpaEligibleUrl(url) {
    if (!url || url.origin !== window.location.origin) return false;
    var path = normalizePath(url.pathname);
    if (path.indexOf(SPA_DASHBOARD_PREFIX) !== 0) return false;
    if (path.indexOf('/dashboard/admin') === 0) return false;
    return true;
  }

  function resetBottomNav() {
    var nav = document.getElementById('v2BottomNav');
    if (!nav) return;
    nav.style.removeProperty('display');
    nav.style.removeProperty('visibility');
    nav.hidden = false;
  }

  function isDashboardShell() {
    return !!document.querySelector('.app');
  }

  function hideBottomNav() {
    var nav = document.getElementById('v2BottomNav');
    if (!nav) return;
    nav.style.setProperty('display', 'none', 'important');
    nav.hidden = true;
  }

  function ensureBottomNavVisible() {
    var nav = document.getElementById('v2BottomNav');
    if (!nav) return;
    if (!isDashboardShell()) {
      hideBottomNav();
      return;
    }
    nav.hidden = false;
    nav.style.removeProperty('visibility');
    if (document.body.classList.contains('v2-exo-session-active')) {
      hideBottomNav();
      return;
    }
    if (window.matchMedia('(max-width: 768px)').matches) {
      nav.style.setProperty('display', 'flex', 'important');
    } else {
      nav.style.removeProperty('display');
    }
  }

  function getNavLabelElement(link) {
    if (!link) return null;
    if (link.classList.contains('v2-bottom-nav__item')) {
      var coachLabel = link.querySelector('.otb-coach-nav-label');
      if (coachLabel) return coachLabel;
      var spans = link.querySelectorAll('span');
      for (var i = spans.length - 1; i >= 0; i--) {
        var sp = spans[i];
        if (sp.classList.contains('v2-bottom-nav__icon-wrap') ||
            sp.classList.contains('v2-bottom-nav__badge') ||
            sp.classList.contains('otb-coach-name')) continue;
        return sp;
      }
      return null;
    }
    if (link.classList.contains('nav-item')) {
      return link.querySelector('span:not(.msg-badge)');
    }
    return null;
  }

  function clearNavLoading() {
    document.querySelectorAll('.otb-nav-loading').forEach(function (el) {
      el.classList.remove('otb-nav-loading');
      el.removeAttribute('aria-busy');
    });
    document.querySelectorAll('.otb-nav-label-loading').forEach(function (el) {
      el.classList.remove('otb-nav-label-loading');
    });
    _navLoadingLink = null;
  }

  function setNavLoading(link) {
    clearNavLoading();
    if (!link) return;
    link.classList.add('otb-nav-loading');
    link.setAttribute('aria-busy', 'true');
    var label = getNavLabelElement(link);
    if (label) label.classList.add('otb-nav-label-loading');
    _navLoadingLink = link;
  }

  window.OTB_ensureBottomNavVisible = ensureBottomNavVisible;
  window.OTB_hideBottomNav = hideBottomNav;

  function isShellNavLink(a) {
    if (!a) return false;
    if (a.classList.contains('v2-bottom-nav__item')) return true;
    return a.classList.contains('nav-item') && !!a.closest('.sidebar');
  }

  function formatBadgeCount(n) {
    n = parseInt(n, 10) || 0;
    if (n <= 0) return '';
    return n > 99 ? '+99' : ('+' + n);
  }

  function updateMessagesNavBadge(count, hideOnMessagesPage, dot) {
    var badge = document.querySelector('[data-otb-nav-badge="messages"]');
    var dotEl = document.querySelector('[data-otb-nav-dot="messages"]');
    var stored = parseInt(count, 10) || 0;
    var showDot = !!dot && stored <= 0;
    if (arguments.length < 3) showDot = !!window.OTB_UNREAD_MSG_DOT && stored <= 0;
    else window.OTB_UNREAD_MSG_DOT = !!dot && stored <= 0;
    window.OTB_UNREAD_MSG_COUNT = stored;
    var display = stored;
    var displayDot = showDot;
    if (hideOnMessagesPage && detectHub(window.location.pathname) === 'messages') {
      display = 0;
      displayDot = false;
    }
    if (badge) {
      badge.textContent = formatBadgeCount(display);
      badge.hidden = display <= 0;
    }
    if (dotEl) {
      dotEl.hidden = !displayDot;
    }
  }

  function maybeStartNavLoading(link) {
    if (!link || !isShellNavLink(link)) return;
    try {
      var parsed = new URL(link.href, window.location.origin);
      if (!isSpaEligibleUrl(parsed)) return;
      var path = normalizePath(parsed.pathname);
      if (path === normalizePath(window.location.pathname) && !parsed.search) return;
      setNavLoading(link);
    } catch (e) {}
  }

  function syncNavBadgesFromDoc(doc) {
    if (!doc) return;
    var badge = doc.querySelector('[data-otb-nav-badge="messages"]');
    var dotEl = doc.querySelector('[data-otb-nav-dot="messages"]');
    var hasDot = !!(dotEl && !dotEl.hidden);
    if (!badge) {
      if (hasDot) updateMessagesNavBadge(0, false, true);
      return;
    }
    if (badge.hidden) {
      updateMessagesNavBadge(0, false, hasDot);
      return;
    }
    var raw = (badge.textContent || '').trim();
    if (raw === '99+' || raw === '+99') {
      updateMessagesNavBadge(99, false, false);
      return;
    }
    updateMessagesNavBadge(raw, false, false);
  }

  function initSidebarNav() {
    var path = normalizePath(window.location.pathname);
    document.querySelectorAll('.sidebar .nav-item[href]').forEach(function (el) {
      try {
        var linkPath = normalizePath(new URL(el.href, window.location.origin).pathname);
        el.classList.toggle('active', linkPath === path);
      } catch (e) {}
    });
  }

  function initBottomNav() {
    resetBottomNav();
    ensureBottomNavVisible();
    var nav = document.getElementById('v2BottomNav');
    if (!nav) return;
    var hub = detectBottomNavHub(window.location.pathname);
    nav.querySelectorAll('.v2-bottom-nav__item').forEach(function (el) {
      var match = hub && el.dataset.hub === hub;
      el.classList.toggle('active', match);
      if (match) el.setAttribute('aria-current', 'page');
      else el.removeAttribute('aria-current');
    });
    updateMessagesNavBadge(window.OTB_UNREAD_MSG_COUNT || 0, true);
  }

  function assetHrefKey(href) {
    if (!href) return '';
    try {
      return new URL(href, window.location.origin).pathname;
    } catch (e) {
      return href.split('?')[0];
    }
  }

  function getAppMain(root) {
    var app = null;
    if (!root || root === document) {
      app = document.querySelector('.app');
    } else if (root.classList && root.classList.contains('app')) {
      app = root;
    } else if (root.querySelector) {
      app = root.querySelector('.app');
    }
    if (!app) return null;
    return app.querySelector(':scope > .main') || app.querySelector('.main');
  }

  function touchCache(path) {
    var i = _cacheOrder.indexOf(path);
    if (i >= 0) _cacheOrder.splice(i, 1);
    _cacheOrder.push(path);
    var guard = 0;
    while (_cacheOrder.length > MAX_CACHE && guard < 20) {
      guard++;
      var old = _cacheOrder.shift();
      if (!old) break;
      if (old === normalizePath(window.location.pathname)) {
        _cacheOrder.push(old);
        continue;
      }
      delete _pageCache[old];
      delete _pagePackages[old];
    }
  }

  function shouldSkipSilentReswap(path) {
    if (document.body.classList.contains('v2-exo-session-active')) return true;
    if (document.body.classList.contains('exam-zen')) return true;
    var hub = detectHub(path || window.location.pathname);
    if (hub === 'astra') return true;
    var ae = document.activeElement;
    if (ae && ae.closest && ae.closest('.view-area, .main')) {
      var tag = (ae.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || ae.isContentEditable) return true;
    }
    return false;
  }

  function collectPageStylesheetHrefs(doc) {
    var hrefs = [];
    doc.querySelectorAll('head link[rel="stylesheet"]').forEach(function (link) {
      var href = link.getAttribute('href');
      if (!isSpaPageStylesheet(href)) return;
      hrefs.push(href);
    });
    return hrefs;
  }

  function pageStyleNodes(root) {
    root = root || document;
    return root.querySelectorAll('head style, body > style');
  }

  function scrubDangerousInlineCss(css) {
    if (!css) return '';
    return css
      .replace(/html\s*,\s*body\s*\{[^}]*\}/gi, '')
      .replace(/html\s*\{[^}]*overflow\s*:\s*hidden[^}]*\}/gi, '')
      .replace(/#v2BottomNav\s*\{[^}]*\}/gi, '')
      .replace(/\.v2-bottom-nav\s*\{[^}]*display\s*:\s*none[^}]*\}/gi, '')
      .replace(/body\.sidebar-open[^{]*\{[^}]*\}/gi, '')
      .trim();
  }

  function scopePageCss(css, scope) {
    scope = scope || '.app > .main';
    css = String(css || '');
    if (!css.trim()) return '';

    function leaveSelector(sel) {
      sel = sel.trim();
      if (!sel) return true;
      if (/^(html|body|:root|:host)\b/i.test(sel)) return true;
      if (sel.indexOf(scope) !== -1) return true;
      if (/#flipFab\b/.test(sel)) return true;
      return false;
    }

    function prefixSelectors(selectorText) {
      return selectorText.split(',').map(function (sel) {
        sel = sel.trim();
        if (!sel) return sel;
        if (leaveSelector(sel)) return sel;
        return scope + ' ' + sel;
      }).join(', ');
    }

    function walk(src) {
      var out = '';
      var i = 0;
      var n = src.length;
      while (i < n) {
        if (src.charCodeAt(i) <= 32) {
          out += src.charAt(i);
          i++;
          continue;
        }
        if (src.charAt(i) === '/' && src.charAt(i + 1) === '*') {
          var cend = src.indexOf('*/', i + 2);
          if (cend < 0) return out + src.slice(i);
          out += src.slice(i, cend + 2);
          i = cend + 2;
          continue;
        }
        var chunkStart = i;
        var isAt = src.charAt(i) === '@';
        var j = i;
        var inStr = '';
        var depth = 0;
        var foundBrace = false;
        var foundSemi = false;
        for (; j < n; j++) {
          var ch = src.charAt(j);
          if (inStr) {
            if (ch === inStr && src.charAt(j - 1) !== '\\') inStr = '';
            continue;
          }
          if (ch === '"' || ch === "'") { inStr = ch; continue; }
          if (ch === '/' && src.charAt(j + 1) === '*') {
            var ce = src.indexOf('*/', j + 2);
            j = ce < 0 ? n : ce + 1;
            continue;
          }
          if (!foundBrace && depth === 0 && ch === ';') {
            foundSemi = true;
            j++;
            break;
          }
          if (ch === '{') {
            foundBrace = true;
            depth++;
          } else if (ch === '}') {
            depth--;
            if (foundBrace && depth === 0) {
              j++;
              break;
            }
          }
        }
        var chunk = src.slice(chunkStart, j);
        i = j;
        if (isAt) {
          var m = chunk.match(/^(@[a-zA-Z\-]+)/);
          var atName = ((m && m[1]) || '').toLowerCase();
          var nestable = atName === '@media' || atName === '@supports' || atName === '@layer' || atName === '@container';
          var passthrough = atName === '@keyframes' || atName === '@-webkit-keyframes' || atName === '@font-face' || atName === '@page' || atName === '@import' || atName === '@charset' || atName === '@namespace';
          if (passthrough || foundSemi || !foundBrace) {
            out += chunk;
            continue;
          }
          if (nestable) {
            var openAt = chunk.indexOf('{');
            var closeAt = chunk.lastIndexOf('}');
            if (openAt < 0 || closeAt < openAt) { out += chunk; continue; }
            out += chunk.slice(0, openAt + 1) + walk(chunk.slice(openAt + 1, closeAt)) + '}';
            continue;
          }
          out += chunk;
          continue;
        }
        if (!foundBrace) {
          out += chunk;
          continue;
        }
        var open = chunk.indexOf('{');
        var close = chunk.lastIndexOf('}');
        out += prefixSelectors(chunk.slice(0, open)) + '{' + chunk.slice(open + 1, close) + '}';
      }
      return out;
    }
    return walk(css);
  }

  function preparePageCss(css) {
    return scopePageCss(scrubDangerousInlineCss(css));
  }

  function extractEmbeddedStyles(html) {
    var chunks = [];
    var cleaned = String(html || '').replace(/<style\b[^>]*>([\s\S]*?)<\/style>/gi, function (_, css) {
      if (css && css.trim()) chunks.push(css);
      return '';
    });
    return { html: cleaned, css: chunks.join('\n') };
  }

  function appendPageCss(css) {
    var text = preparePageCss(css);
    if (!text) return;
    var s = document.querySelector('head style[data-otb-spa="page"]:not([data-otb-href])');
    if (!s) {
      s = document.createElement('style');
      s.setAttribute('data-otb-spa', 'page');
      document.head.appendChild(s);
    }
    s.textContent = ((s.textContent || '') + '\n' + text).trim();
  }

  function dedupeSidebars() {
    var app = document.querySelector('.app');
    if (!app) return;
    var keep = app.querySelector(':scope > .sidebar');
    document.querySelectorAll('.sidebar').forEach(function (el) {
      if (el !== keep) el.remove();
    });
  }

  function isProtectedHeadStyle(style) {
    if (!style) return true;
    var id = String(style.id || '').toLowerCase();
    if (id === 'otb-base-css') return true;
    if (id.indexOf('mjx') >= 0 || id.indexOf('mathjax') >= 0 || id.indexOf('katex') >= 0) return true;
    var cls = String(style.className || '').toLowerCase();
    if (cls.indexOf('mjx') >= 0 || cls.indexOf('katex') >= 0) return true;
    return false;
  }

  function collectPageInlineCss(doc) {
    var chunks = [];
    pageStyleNodes(doc).forEach(function (style) {
      if (isProtectedHeadStyle(style)) return;
      var text = scrubDangerousInlineCss((style.textContent || '').trim());
      if (text) chunks.push(text);
    });
    return chunks.join('\n');
  }

  function stripPageInlineCss() {
    document.querySelectorAll('head style[data-otb-spa]:not([data-otb-href]), body > style').forEach(function (style) {
      if (isProtectedHeadStyle(style)) return;
      style.remove();
    });
  }

  function tagInitialPageCss() {
    pageStyleNodes(document).forEach(function (style) {
      if (isProtectedHeadStyle(style)) return;
      style.setAttribute('data-otb-spa', 'page');
    });
  }

  function findStylesheetLink(key) {
    var found = null;
    document.querySelectorAll('head link[rel="stylesheet"]').forEach(function (link) {
      if (assetHrefKey(link.getAttribute('href') || '') === key) found = link;
    });
    return found;
  }

  function loadStylesheet(href) {
    var key = assetHrefKey(href);
    var existing = findStylesheetLink(key);
    if (existing) {
      try {
        if (existing.sheet) return Promise.resolve();
      } catch (e) {}
      return new Promise(function (resolve) {
        existing.addEventListener('load', resolve, { once: true });
        existing.addEventListener('error', resolve, { once: true });
      });
    }
    return new Promise(function (resolve) {
      var l = document.createElement('link');
      l.rel = 'stylesheet';
      l.href = href;
      l.setAttribute('data-otb-spa', 'page');
      l.addEventListener('load', resolve, { once: true });
      l.addEventListener('error', resolve, { once: true });
      document.head.appendChild(l);
    });
  }

  function injectInlinePageCss(pkg) {
    stripPageInlineCss();
    if (pkg && pkg.inlineCss) {
      var s = document.createElement('style');
      s.setAttribute('data-otb-spa', 'page');
      s.textContent = preparePageCss(pkg.inlineCss);
      document.head.appendChild(s);
    }
  }

  function ensurePackageStyles(pkg) {
    if (!pkg) return Promise.resolve();
    var hrefs = pkg.stylesheetHrefs || [];
    if (!hrefs.length) return Promise.resolve();
    return Promise.all(hrefs.map(loadStylesheet));
  }

  function injectPageAssetsFromPackage(pkg) {
    if (!pkg) return;
    var newHrefs = pkg.stylesheetHrefs || [];
    var keep = {};
    newHrefs.forEach(function (href) {
      keep[assetHrefKey(href)] = true;
    });

    document.querySelectorAll('head link[rel="stylesheet"]').forEach(function (link) {
      var href = link.getAttribute('href') || '';
      if (!isSpaPageStylesheet(href)) return;
      if (!keep[assetHrefKey(href)]) link.remove();
    });
    document.querySelectorAll('head style[data-otb-href]').forEach(function (style) {
      var key = style.getAttribute('data-otb-href') || '';
      if (!keep[key]) style.remove();
    });

    newHrefs.forEach(function (href) {
      loadStylesheet(href);
    });

    injectInlinePageCss(pkg);
  }

  function resetSpaBodyState() {
    SPA_BODY_CLASSES.forEach(function (cls) {
      document.body.classList.remove(cls);
    });
    if (typeof window.OTB_closeSidebar === 'function') {
      window.OTB_closeSidebar();
    }
    var overlay = document.querySelector('.mob-overlay');
    if (overlay) overlay.style.removeProperty('display');
    lockDesktopSidebar();
    resetBottomNav();
    ensureBottomNavVisible();
  }

  function lockDesktopSidebar() {
    dedupeSidebars();
    var sidebar = document.querySelector('.app > .sidebar') || document.querySelector('.sidebar');
    if (!sidebar) return;
    sidebar.style.removeProperty('transform');
    sidebar.style.removeProperty('position');
    sidebar.style.removeProperty('top');
    sidebar.style.removeProperty('left');
    sidebar.style.removeProperty('bottom');
    sidebar.style.removeProperty('width');
    sidebar.style.removeProperty('z-index');
    sidebar.style.removeProperty('display');
    if (window.matchMedia && window.matchMedia('(min-width: 769px)').matches) {
      sidebar.style.removeProperty('visibility');
      document.body.classList.remove('sidebar-open');
    }
  }

  function scriptAlreadyLoaded(src) {
    if (!src) return false;
    try {
      var abs = new URL(src, window.location.origin).href;
      if (_loadedScriptSrcs[abs]) return true;
      return !!document.querySelector('script[src="' + src + '"]');
    } catch (e) {
      return false;
    }
  }

  function collectPageScripts(doc) {
    var result = [];
    var seen = new Set();
    var app = doc.querySelector('.app');
    if (!app) return result;

    function pushScript(scriptEl) {
      if (!scriptEl || isPermanentScript(scriptEl)) return;
      var key = scriptEl.getAttribute('src') || scriptEl.textContent || '';
      if (seen.has(key)) return;
      seen.add(key);
      result.push(scriptEl);
    }

    app.querySelectorAll('script').forEach(pushScript);

    var guestToast = doc.getElementById('guestBlockedToast');
    var node = app.nextElementSibling;
    while (node && node !== guestToast) {
      if (node.tagName === 'SCRIPT') pushScript(node);
      node = node.nextElementSibling;
    }

    var pageScriptsHolder = doc.getElementById('otb-page-scripts');
    if (pageScriptsHolder) {
      pageScriptsHolder.querySelectorAll('script').forEach(pushScript);
    }

    return result;
  }

  function serializeScripts(scriptNodes) {
    return (scriptNodes || []).map(function (s) {
      return {
        src: s.getAttribute('src') || '',
        text: s.src ? '' : (s.textContent || '')
      };
    });
  }

  function syncPageScriptsHolderFromPackage(pkg) {
    var curHolder = document.getElementById('otb-page-scripts');
    if (!curHolder) return;
    curHolder.innerHTML = pkg.pageScriptsHtml || '';
  }

  function runSerializedScripts(scripts) {
    if (!scripts || !scripts.length) return;
    scripts.forEach(function (item) {
      if (item.src) {
        if (scriptAlreadyLoaded(item.src)) {
          document.dispatchEvent(new CustomEvent('otb:spa-script-loaded', { detail: { src: item.src } }));
          return;
        }
        var s = document.createElement('script');
        s.src = item.src;
        s.async = false;
        try {
          _loadedScriptSrcs[new URL(item.src, window.location.origin).href] = true;
        } catch (e) {}
        document.body.appendChild(s);
        return;
      }
      if (!item.text) return;
      // SPA : const/let globaux se redéclarent → SyntaxError. On les réécrit en var
      // (même scope script) pour garder les function declarations globales (onclick).
      var text = String(item.text).replace(/(^|\n)([ \t]*)(const|let)[ \t]+/g, '$1$2var ');
      var inline = document.createElement('script');
      inline.textContent = text;
      try {
        document.body.appendChild(inline);
      } catch (e) {
        console.warn('SPA script error:', e);
      }
    });
  }

  function buildPagePackage(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    var app = doc.querySelector('.app');
    if (!app) return null;

    var main = getAppMain(app);
    var pageScriptsHolder = doc.getElementById('otb-page-scripts');
    var badge = doc.querySelector('[data-otb-nav-badge="messages"]');
    var inlineCss = collectPageInlineCss(doc);
    var mainExtract = extractEmbeddedStyles(main ? main.innerHTML : '');
    if (mainExtract.css) inlineCss = (inlineCss + '\n' + mainExtract.css).trim();

    return {
      appClass: app.className,
      appHTML: app.innerHTML,
      mainHTML: main ? mainExtract.html : app.innerHTML,
      hasMain: !!main,
      title: doc.title || '',
      scripts: serializeScripts(collectPageScripts(doc)),
      pageScriptsHtml: pageScriptsHolder ? pageScriptsHolder.innerHTML : '',
      stylesheetHrefs: collectPageStylesheetHrefs(doc),
      inlineCss: inlineCss,
      unreadMsg: badge && !badge.hidden ? (badge.textContent || '').trim() : '0',
      unreadDot: !!(doc.querySelector('[data-otb-nav-dot="messages"]') && !doc.querySelector('[data-otb-nav-dot="messages"]').hidden)
    };
  }

  function storePageHtml(path, html) {
    var pkg = buildPagePackage(html);
    if (!pkg) return;
    _pageCache[path] = html;
    _pagePackages[path] = pkg;
    touchCache(path);
  }

  function fetchPageHtml(path, opts) {
    opts = opts || {};
    var force = !!opts.force;
    var signal = opts.signal;
    if (!force && _pageCache[path] && _pagePackages[path]) {
      return Promise.resolve(_pageCache[path]);
    }
    if (_fetchInflight[path] && !signal) {
      return _fetchInflight[path];
    }
    var req = fetch(path, {
      credentials: 'same-origin',
      headers: { 'X-OTB-SPA': '1' },
      signal: signal || undefined
    })
      .then(function (r) {
        if (!r.ok) throw new Error('fetch failed');
        return r.text();
      })
      .then(function (html) {
        storePageHtml(path, html);
        return html;
      })
      .finally(function () {
        if (_fetchInflight[path] === req) delete _fetchInflight[path];
      });
    _fetchInflight[path] = req;
    return req;
  }

  function warmPageFetch(path) {
    path = normalizePath(path);
    if (_pagePackages[path] || _warming[path]) return Promise.resolve();
    if (path === normalizePath(window.location.pathname)) return Promise.resolve();
    _warming[path] = true;
    return fetchPageHtml(path).catch(function () {}).finally(function () {
      delete _warming[path];
    });
  }

  function startIdlePrefetch() {
    var i = 0;
    function next() {
      if (document.hidden) {
        document.addEventListener('visibilitychange', function onVis() {
          if (!document.hidden) {
            document.removeEventListener('visibilitychange', onVis);
            schedule();
          }
        });
        return;
      }
      if (_navInProgress) {
        setTimeout(schedule, 400);
        return;
      }
      while (i < WARM_PRIORITY.length) {
        var p = normalizePath(WARM_PRIORITY[i++]);
        if (_pagePackages[p]) continue;
        if (p === normalizePath(window.location.pathname)) continue;
        warmPageFetch(p).finally(schedule);
        return;
      }
      if (window.OTBMsgCache && typeof window.OTBMsgCache.warmup === 'function') {
        window.OTBMsgCache.warmup();
      }
    }
    function schedule() {
      if (window.requestIdleCallback) {
        requestIdleCallback(next, { timeout: 2500 });
      } else {
        setTimeout(next, 450);
      }
    }
    schedule();
  }

  function warmPage(pathOrUrl) {
    var path = normalizePath(pathOrUrl);
    warmPageFetch(path);
  }

  function cacheCurrentPage() {
    var path = normalizePath(window.location.pathname);
    var app = document.querySelector('.app');
    if (!app || _pagePackages[path]) return;
    var main = getAppMain(app);
    var pageScriptsHolder = document.getElementById('otb-page-scripts');
    var hrefs = [];
    document.querySelectorAll('head link[rel="stylesheet"]').forEach(function (link) {
      var href = link.getAttribute('href');
      if (isSpaPageStylesheet(href)) hrefs.push(href);
    });
    var inlineChunks = [];
    document.querySelectorAll('head style, body > style').forEach(function (style) {
      if (isProtectedHeadStyle(style)) return;
      if (style.getAttribute('data-otb-href')) return;
      var text = scrubDangerousInlineCss((style.textContent || '').trim());
      if (text) inlineChunks.push(text);
    });
    var scriptNodes = pageScriptsHolder
      ? Array.prototype.slice.call(pageScriptsHolder.querySelectorAll('script'))
      : [];
    var storedMain = main ? main.innerHTML : app.innerHTML;
    if (main) {
      var extracted = extractEmbeddedStyles(storedMain);
      storedMain = extracted.html;
      if (extracted.css) inlineChunks.push(extracted.css);
    }
    _pagePackages[path] = {
      appClass: app.className,
      appHTML: app.innerHTML,
      mainHTML: storedMain,
      hasMain: !!main,
      title: document.title || '',
      scripts: serializeScripts(scriptNodes),
      pageScriptsHtml: pageScriptsHolder ? pageScriptsHolder.innerHTML : '',
      stylesheetHrefs: hrefs,
      inlineCss: inlineChunks.join('\n'),
      unreadMsg: '0'
    };
    touchCache(path);
  }

  function reinitShellDeferred() {
    requestAnimationFrame(function () {
      if (window.OTB_closeSidebar) window.OTB_closeSidebar();
      if (window.OTB_initMobileSidebar) window.OTB_initMobileSidebar();
      if (window.MathJax && window.MathJax.typesetPromise) {
        var target = document.querySelector('.view-area') || document.querySelector('.main') || document.body;
        window.MathJax.typesetPromise([target]).catch(function () {});
      }
    });
  }

  function coachNavLabel(name) {
    name = (name || '').trim();
    if (!name) return 'Chat IA';
    return name.length <= 10 ? name : 'Chat IA';
  }

  function updateCoachLabels() {
    var name = window.OTB_COACH_NAME || '';
    var navLabel = coachNavLabel(name);
    document.querySelectorAll('.otb-coach-name').forEach(function (el) {
      el.textContent = name || 'Chat IA';
    });
    document.querySelectorAll('.otb-coach-nav-label').forEach(function (el) {
      el.textContent = navLabel;
    });
    var navCoach = document.querySelector('.v2-bottom-nav__item[data-hub="astra"] .otb-coach-nav-label');
    if (navCoach) navCoach.textContent = navLabel;
    var navLink = document.querySelector('.v2-bottom-nav__item[data-hub="astra"]');
    if (navLink && name) navLink.title = name;
  }

  function applyPagePackage(pkg, path, push, fetchUrl, opts) {
    if (!pkg) return false;
    var app = document.querySelector('.app');
    if (!app) return false;
    opts = opts || {};

    resetSpaBodyState();

    ensurePackageStyles(pkg).then(function () {
      injectPageAssetsFromPackage(pkg);

      var sidebar = app.querySelector(':scope > .sidebar') || app.querySelector('.sidebar');
      var appClass = pkg.appClass || app.className || 'app';
      if ((pkg.mainHTML || '').indexOf('topbar') !== -1 && (' ' + appClass + ' ').indexOf(' has-topbar ') === -1) {
        appClass = (appClass + ' has-topbar').replace(/\s+/g, ' ').trim();
      }
      app.className = appClass;
      lockDesktopSidebar();

      var main = getAppMain(app);
      if (main && pkg.mainHTML != null) {
        var extracted = extractEmbeddedStyles(pkg.mainHTML);
        main.innerHTML = extracted.html;
        if (extracted.css) appendPageCss(extracted.css);
      } else if (sidebar) {
        var kept = sidebar;
        var incoming = extractEmbeddedStyles(pkg.appHTML || '');
        app.innerHTML = incoming.html;
        if (incoming.css) appendPageCss(incoming.css);
        var incomingSidebar = app.querySelector('.sidebar');
        if (incomingSidebar) incomingSidebar.replaceWith(kept);
        else app.insertBefore(kept, app.firstChild);
      } else {
        var raw = extractEmbeddedStyles(pkg.appHTML || '');
        app.innerHTML = raw.html;
        if (raw.css) appendPageCss(raw.css);
      }
      dedupeSidebars();
      lockDesktopSidebar();

      syncPageScriptsHolderFromPackage(pkg);

      if (pkg.title) document.title = pkg.title;
      if (push) history.pushState({ otbSpa: true }, '', fetchUrl);

      if (pkg.unreadMsg) {
        if (pkg.unreadMsg === '99+' || pkg.unreadMsg === '+99') updateMessagesNavBadge(99, false, !!pkg.unreadDot);
        else updateMessagesNavBadge(pkg.unreadMsg, false, !!pkg.unreadDot);
      } else if (pkg.unreadDot) {
        updateMessagesNavBadge(0, false, true);
      }

      initBottomNav();
      initSidebarNav();
      updateCoachLabels();

      var viewArea = document.querySelector('.view-area');
      if (viewArea) viewArea.scrollTop = 0;

      runSerializedScripts(pkg.scripts);

      requestAnimationFrame(function () {
        reinitShellDeferred();
        ensureBottomNavVisible();
        setTimeout(ensureBottomNavVisible, 0);
        setTimeout(ensureBottomNavVisible, 120);
        clearNavLoading();
        if (typeof window.OTB_onSpaNavigate === 'function') window.OTB_onSpaNavigate(path);
        document.dispatchEvent(new CustomEvent('otb:spa-navigate', { detail: { path: path } }));
        initPageInfo(document);
      });
    });

    return true;
  }

  function revalidatePath(path, fetchUrl, shownPkg, token) {
    fetchPageHtml(path, { force: true }).then(function () {
      if (token !== _navGen) return;
      if (normalizePath(window.location.pathname) !== path) return;
      if (shouldSkipSilentReswap(path)) return;
      var fresh = _pagePackages[path];
      if (!fresh) return;
      if (shownPkg && fresh.mainHTML === shownPkg.mainHTML) return;
      applyPagePackage(fresh, path, false, fetchUrl, { silent: true });
    }).catch(function () {});
  }

  function navigateTo(url, push, sourceLink) {
    var parsed = new URL(url, window.location.origin);
    var path = normalizePath(parsed.pathname);
    if (!isSpaEligibleUrl(parsed)) {
      clearNavLoading();
      window.location.href = url;
      return;
    }
    if (path === normalizePath(window.location.pathname) && !parsed.search) {
      clearNavLoading();
      return;
    }

    var shell = document.querySelector('.app') || document.querySelector('.main');
    if (!shell) {
      clearNavLoading();
      window.location.href = url;
      return;
    }

    if (sourceLink && isShellNavLink(sourceLink)) setNavLoading(sourceLink);

    var fetchUrl = parsed.pathname + parsed.search;
    var pkg = _pagePackages[path];

    _navGen += 1;
    var token = _navGen;
    if (_navAbort) {
      try { _navAbort.abort(); } catch (e) {}
      if (_navFetchPath) delete _fetchInflight[_navFetchPath];
    }
    _navAbort = new AbortController();
    _navFetchPath = path;

    if (pkg) {
      applyPagePackage(pkg, path, push, fetchUrl);
      revalidatePath(path, fetchUrl, pkg, token);
      return;
    }

    _navInProgress = true;
    var pending = _fetchInflight[path];
    (pending || fetchPageHtml(path, { signal: _navAbort.signal }))
      .then(function (html) {
        if (token !== _navGen) return;
        if (html) storePageHtml(path, html);
        if (!_pagePackages[path]) throw new Error('spa package missing');
        applyPagePackage(_pagePackages[path], path, push, fetchUrl);
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        if (token !== _navGen) return;
        clearNavLoading();
        if (!_pagePackages[path]) window.location.href = url;
      })
      .finally(function () {
        if (token === _navGen) _navInProgress = false;
      });
  }

  function shouldHandleSpaClick(e, a) {
    if (!a) return false;
    if (a.hasAttribute('data-otb-full-nav')) return false;
    if (a.dataset && a.dataset.otbFullNav) return false;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
    if (a.target && a.target !== '_self') return false;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#') return false;
    try {
      return isSpaEligibleUrl(new URL(href, window.location.origin));
    } catch (err) {
      return false;
    }
  }

  function initPageInfo(root) {
    var scope = root || document;
    scope.querySelectorAll('[data-otb-info]').forEach(function (section) {
      var key = section.getAttribute('data-otb-info');
      if (!key) return;
      var storageKey = 'otb-info-seen:' + key;
      var modal = scope.querySelector('[data-otb-info-modal="' + key + '"]')
        || document.querySelector('[data-otb-info-modal="' + key + '"]');
      var seen = false;
      try { seen = localStorage.getItem(storageKey) === '1'; } catch (err) {}

      function dismiss() {
        try { localStorage.setItem(storageKey, '1'); } catch (err) {}
        if (modal) modal.hidden = true;
        section.hidden = false;
        document.body.classList.remove('otb-info-open');
      }

      if (!seen && modal) {
        section.hidden = true;
        modal.hidden = false;
        document.body.classList.add('otb-info-open');
        modal.querySelectorAll('[data-otb-info-close]').forEach(function (btn) {
          btn.onclick = dismiss;
        });
      } else {
        section.hidden = false;
        if (modal) modal.hidden = true;
      }
    });
  }

  function initSpaLinkCapture() {
    document.addEventListener('pointerdown', function (e) {
      if (e.button && e.button !== 0) return;
      var a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      try {
        var url = new URL(a.href, window.location.origin);
        if (isSpaEligibleUrl(url)) warmPage(url.pathname + url.search);
      } catch (err) {}
    }, { passive: true });

    document.addEventListener('click', function (e) {
      var a = e.target.closest('a[href]');
      if (!shouldHandleSpaClick(e, a)) return;
      e.preventDefault();
      setNavLoading(a);
      navigateTo(a.href, true, a);
    });
  }

  function callPageFnWhenReady(name, args) {
    var n = 0;
    (function tick() {
      var fn = window[name];
      if (typeof fn === 'function') {
        fn.apply(null, args);
        return;
      }
      if (n++ < 40) setTimeout(tick, 25);
    })();
  }

  function initExamTileDelegation() {
    document.addEventListener('click', function (e) {
      var restart = e.target.closest('[data-exam-restart]');
      if (restart) {
        e.preventDefault();
        callPageFnWhenReady('startExamen', [window.currentSubject]);
        return;
      }
      var tile = e.target.closest('[data-exam-subject]');
      if (!tile) return;
      e.preventDefault();
      callPageFnWhenReady('startExamen', [tile.getAttribute('data-exam-subject')]);
    });
  }

  function initSpaNav() {
    initSpaLinkCapture();
    initExamTileDelegation();

    window.addEventListener('popstate', function (e) {
      if (e.state && e.state.otbSpa) {
        navigateTo(window.location.pathname + window.location.search, false);
      }
    });

    window.addEventListener('resize', function () {
      ensureBottomNavVisible();
    });

    document.addEventListener('otb:spa-navigate', function () {
      ensureBottomNavVisible();
    });

    startIdlePrefetch();
  }

  function init() {
    var app = document.querySelector('.app');
    if (!app) {
      hideBottomNav();
      document.body.classList.remove('otb-shell');
      return;
    }
    app.classList.remove('otb-spa-pending');
    document.body.classList.add('otb-shell');
    tagInitialPageCss();
    cacheCurrentPage();
    var mainEl = getAppMain(document);
    if (mainEl && !mainEl.id) mainEl.id = 'app-main';
    initBottomNav();
    initSidebarNav();
    initSpaNav();
    updateCoachLabels();
    initPageInfo(document);
    history.replaceState({ otbSpa: true }, '', window.location.pathname + window.location.search);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
