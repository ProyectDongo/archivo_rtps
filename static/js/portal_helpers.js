/* ==========================================================================
   Helpers compartidos del portal (CSP-safe, sin dependencias externas).
   ========================================================================== */

window.PM = window.PM || {};

(function () {
  'use strict';

  const meta = document.querySelector('meta[name="csrf-token"]');
  PM.csrf = meta ? meta.content : '';

  // ─── fetch wrapper con CSRF ────────────────────────────────────────────
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

  // ─── Debounce ──────────────────────────────────────────────────────────
  PM.debounce = function (fn, ms) {
    let t;
    return function () {
      const args = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  };

  // ─── Toast ────────────────────────────────────────────────────────────
  // PM.toast(msg, { duration, action, onAction, onEnd })
  // Devuelve { dismiss() }.
  PM.toast = function (msg, opts) {
    opts = opts || {};
    const area = document.getElementById('toast-area');
    if (!area) return { dismiss: function () {} };

    const el = document.createElement('div');
    el.className = 'toast';
    const msgEl = document.createElement('span');
    msgEl.textContent = msg;
    el.appendChild(msgEl);

    let cancelled = false;

    if (opts.action && opts.onAction) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'toast-undo';
      btn.textContent = opts.action;
      btn.addEventListener('click', function () {
        cancelled = true;
        clearTimeout(timer);
        opts.onAction();
        _dismissToast(el);
      });
      el.appendChild(btn);
    }

    area.appendChild(el);

    const timer = setTimeout(function () {
      _dismissToast(el);
      if (!cancelled && opts.onEnd) opts.onEnd();
    }, opts.duration || 4000);

    return {
      dismiss: function () { clearTimeout(timer); _dismissToast(el); }
    };
  };

  function _dismissToast(el) {
    if (!el.parentNode) return;
    el.classList.add('toast-out');
    setTimeout(function () { if (el.parentNode) el.remove(); }, 220);
  }

  // ─── Confirm modal (reemplaza window.confirm) ──────────────────────────
  // PM.confirm(msg) → Promise<boolean>
  PM.confirm = function (msg) {
    return new Promise(function (resolve) {
      const modal   = document.getElementById('confirm-modal');
      const msgEl   = document.getElementById('confirm-modal-msg');
      const okBtn   = document.getElementById('confirm-modal-ok');
      const cancelBtn = document.getElementById('confirm-modal-cancel');

      if (!modal) { resolve(window.confirm(msg)); return; }

      msgEl.textContent = msg;
      modal.hidden = false;
      okBtn.focus();

      function cleanup(result) {
        modal.hidden = true;
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        document.removeEventListener('keydown', onKey);
        resolve(result);
      }
      function onOk() { cleanup(true); }
      function onCancel() { cleanup(false); }
      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); cleanup(false); }
        if (e.key === 'Enter')  { e.preventDefault(); cleanup(true); }
      }

      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      document.addEventListener('keydown', onKey);
      modal.addEventListener('click', function (e) {
        if (e.target === modal) cleanup(false);
      }, { once: true });
    });
  };

  // ─── data-confirm → modal async ────────────────────────────────────────
  let _skipConfirm = false;
  document.addEventListener('submit', function (e) {
    const form = e.target;
    if (!form || form.tagName !== 'FORM') return;
    if (_skipConfirm) return;
    const msg = form.getAttribute('data-confirm');
    if (!msg) return;
    e.preventDefault();
    PM.confirm(msg).then(function (ok) {
      if (!ok) return;
      _skipConfirm = true;
      if (form.requestSubmit) form.requestSubmit();
      else form.submit();
      setTimeout(function () { _skipConfirm = false; }, 100);
    });
  }, true);

})();

// ─── Auto-resize iframes de email ──────────────────────────────────────────
function _resizeEmailIframe(iframe) {
  try {
    const doc = iframe.contentDocument || iframe.contentWindow.document;
    const h = doc.documentElement.scrollHeight || doc.body.scrollHeight;
    iframe.style.height = Math.max(h + 20, 60) + 'px';
  } catch (e) {}
}
window._resizeEmailIframe = _resizeEmailIframe;

document.addEventListener('load', function (e) {
  if (e.target && e.target.classList && e.target.classList.contains('email-iframe')) {
    _resizeEmailIframe(e.target);
  }
}, true);
