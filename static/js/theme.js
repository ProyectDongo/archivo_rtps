// Toggle de tema (light/dark) y densidad (comfy/compact). Persiste en localStorage.
// El estado inicial ya fue aplicado por el inline script en <head> (anti-FOUC).
(function () {
  'use strict';

  const root = document.documentElement;
  const THEMES = ['light', 'dark'];
  const DENSITIES = ['comfy', 'compact'];

  function getTheme()    { return root.getAttribute('data-theme')   === 'dark'    ? 'dark'    : 'light'; }
  function getDensity()  { return root.getAttribute('data-density') === 'compact' ? 'compact' : 'comfy'; }

  function setTheme(t) {
    if (!THEMES.includes(t)) return;
    if (t === 'dark') root.setAttribute('data-theme', 'dark');
    else root.removeAttribute('data-theme');
    try { localStorage.setItem('pm.theme', t); } catch (e) {}
    syncBtns();
  }

  function setDensity(d) {
    if (!DENSITIES.includes(d)) return;
    if (d === 'compact') root.setAttribute('data-density', 'compact');
    else root.removeAttribute('data-density');
    try { localStorage.setItem('pm.density', d); } catch (e) {}
    syncBtns();
  }

  function syncBtns() {
    const t = getTheme();
    const d = getDensity();
    document.querySelectorAll('[data-theme-set]').forEach(b => {
      b.classList.toggle('is-active', b.getAttribute('data-theme-set') === t);
      b.setAttribute('aria-pressed', b.getAttribute('data-theme-set') === t ? 'true' : 'false');
    });
    document.querySelectorAll('[data-density-set]').forEach(b => {
      b.classList.toggle('is-active', b.getAttribute('data-density-set') === d);
      b.setAttribute('aria-pressed', b.getAttribute('data-density-set') === d ? 'true' : 'false');
    });
  }

  document.addEventListener('click', function (e) {
    const tBtn = e.target.closest('[data-theme-set]');
    if (tBtn) { setTheme(tBtn.getAttribute('data-theme-set')); return; }
    const dBtn = e.target.closest('[data-density-set]');
    if (dBtn) { setDensity(dBtn.getAttribute('data-density-set')); return; }
  });

  syncBtns();
})();
