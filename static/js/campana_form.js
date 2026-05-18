/**
 * UI del formulario de campañas:
 *   - Grid de días del mes con multi-select sincronizado al input hidden.
 *   - Modal de "Enviar test" con POST AJAX al endpoint campana_test.
 *
 * Depende de PM.csrf() / PM.post() (portal_helpers.js).
 */
(function () {
  'use strict';

  // ─── Grid de días del mes ─────────────────────────────────────────────────
  const checks = Array.from(document.querySelectorAll('.dia-check'));
  const hidden = document.getElementById('dias-input');
  if (checks.length && hidden) {
    const seleccionados = new Set();
    try {
      const inicial = JSON.parse(hidden.value || '[]');
      if (Array.isArray(inicial)) inicial.forEach((d) => seleccionados.add(+d));
    } catch (e) {
      // ignore
    }

    function pintar(cb) {
      const lbl = cb.parentElement.querySelector('.dia-label');
      if (!lbl) return;
      if (cb.checked) {
        lbl.classList.add('bg-primary', 'text-white', 'border-primary');
        lbl.classList.remove('bg-white', 'text-gray-mid');
      } else {
        lbl.classList.remove('bg-primary', 'text-white', 'border-primary');
        lbl.classList.add('bg-white', 'text-gray-mid');
      }
    }

    function syncHidden() {
      const arr = checks.filter((c) => c.checked).map((c) => +c.value).sort((a, b) => a - b);
      hidden.value = JSON.stringify(arr);
    }

    checks.forEach((cb) => {
      if (seleccionados.has(+cb.value)) cb.checked = true;
      pintar(cb);
      cb.addEventListener('change', () => {
        pintar(cb);
        syncHidden();
      });
    });
    syncHidden();
  }

  // ─── Modal de test ────────────────────────────────────────────────────────
  const modal = document.getElementById('test-modal');
  const btnOpen = document.getElementById('btn-test');
  if (!modal || !btnOpen) return;

  const btnCancel = document.getElementById('test-cancel');
  const btnSend = document.getElementById('test-send');
  const input = document.getElementById('test-email');
  const status = document.getElementById('test-status');

  function open() {
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    input.value = '';
    status.classList.add('hidden');
    status.textContent = '';
    setTimeout(() => input.focus(), 100);
  }
  function close() {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }

  btnOpen.addEventListener('click', open);
  btnCancel.addEventListener('click', close);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) close();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) close();
  });

  btnSend.addEventListener('click', () => {
    const email = (input.value || '').trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      status.textContent = 'Email inválido.';
      status.className = 'text-xs text-red-600 mb-3';
      status.classList.remove('hidden');
      return;
    }
    btnSend.disabled = true;
    btnSend.textContent = 'Enviando…';
    status.classList.add('hidden');

    const url = btnSend.dataset.url || window.location.pathname.replace(/\/$/, '') + '/test/';
    PM.post(url, { email })
      .then((resp) => {
        if (resp && resp.ok) {
          status.textContent = resp.mensaje || 'Email de prueba enviado.';
          status.className = 'text-xs text-primary-dark mb-3';
        } else {
          status.textContent = (resp && resp.error) || 'Error al enviar.';
          status.className = 'text-xs text-red-600 mb-3';
        }
        status.classList.remove('hidden');
      })
      .catch((e) => {
        status.textContent = 'Error de red: ' + e.message;
        status.className = 'text-xs text-red-600 mb-3';
        status.classList.remove('hidden');
      })
      .finally(() => {
        btnSend.disabled = false;
        btnSend.textContent = 'Enviar';
      });
  });

  // Set la URL del endpoint en el botón (la sabemos por la URL actual)
  const m = window.location.pathname.match(/\/campanas\/(\d+)\//);
  if (m) btnSend.dataset.url = `/intranet/campanas/${m[1]}/test/`;
})();
