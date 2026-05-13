/* ==========================================================================
   Helpers compartidos del portal (CSP-safe, sin dependencias).
   ========================================================================== */

window.PM = window.PM || {};

(function () {
  'use strict';

  // CSRF token desde <meta name="csrf-token">
  const meta = document.querySelector('meta[name="csrf-token"]');
  PM.csrf = meta ? meta.content : '';

  // Wrapper de fetch que agrega CSRF + same-origin
  PM.post = function (url, params) {
    const body = new URLSearchParams();
    if (params) Object.keys(params).forEach(function (k) { body.append(k, params[k]); });
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': PM.csrf,
        'X-Requested-With': 'fetch',
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: body.toString(),
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  };

  // Debounce simple para autosave de notas
  PM.debounce = function (fn, ms) {
    let t;
    return function () {
      const args = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  };

  // Confirm dialog para forms con data-confirm (reemplaza onsubmit inline).
  // Se delega en document para cubrir forms inyectados dinámicamente.
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || form.tagName !== 'FORM') return;
    var msg = form.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) e.preventDefault();
  }, true);
})();
