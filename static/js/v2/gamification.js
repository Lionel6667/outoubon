/**
 * OU TOU BON V2 — Gamification display helpers (Phase 1)
 * Read-only UI updates from context-injected globals. No XP formula changes.
 */
(function () {
  'use strict';

  function initXpStrip() {
    var strip = document.querySelector('.v2-xp-strip');
    if (!strip) return;
    var xp = window.OTB_USER_XP;
    var pct = window.OTB_XP_TIER_PCT;
    if (typeof xp === 'number') {
      var xpEl = strip.querySelector('[data-v2-xp]');
      if (xpEl) xpEl.textContent = xp.toLocaleString('fr-FR') + ' XP';
    }
    if (typeof pct === 'number') {
      var fill = strip.querySelector('.v2-xp-strip__bar-fill');
      if (fill) fill.style.width = Math.min(100, Math.max(0, pct)) + '%';
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initXpStrip);
  } else {
    initXpStrip();
  }
})();
