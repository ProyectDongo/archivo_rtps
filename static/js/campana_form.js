/**
 * UI del formulario de campañas:
 *   - Grids de días/meses con multi-select sincronizado a inputs hidden.
 *   - Modal "Enviar test" con POST AJAX.
 *   - Botón "Ejecutar ahora" con feedback en vivo.
 *   - Auto-refresh del log de envíos cada 6 segundos.
 *
 * Depende de PM.csrf() / PM.post() (portal_helpers.js).
 */
(function () {
  'use strict';

  // ─── Helper genérico para grids tipo "días" / "meses" ─────────────────────
  function initGrid(checkClass, labelClass, hiddenId) {
    const checks = Array.from(document.querySelectorAll('.' + checkClass));
    const hidden = document.getElementById(hiddenId);
    if (!checks.length || !hidden) return;

    const seleccionados = new Set();
    try {
      const inicial = JSON.parse(hidden.value || '[]');
      if (Array.isArray(inicial)) inicial.forEach((d) => seleccionados.add(+d));
    } catch (e) { /* ignore */ }

    function pintar(cb) {
      const lbl = cb.parentElement.querySelector('.' + labelClass);
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
      cb.addEventListener('change', () => { pintar(cb); syncHidden(); });
    });
    syncHidden();
  }

  initGrid('dia-check', 'dia-label', 'dias-input');
  initGrid('mes-check', 'mes-label', 'meses-input');

  // ─── Modal de test ────────────────────────────────────────────────────────
  const modal = document.getElementById('test-modal');
  const btnOpen = document.getElementById('btn-test');
  if (modal && btnOpen) {
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
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
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

      const m = window.location.pathname.match(/\/campanas\/(\d+)\//);
      const url = m ? `/intranet/campanas/${m[1]}/test/` : null;
      if (!url) return;

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
  }

  // ─── Botón "Ejecutar ahora" ───────────────────────────────────────────────
  const btnExec = document.getElementById('btn-ejecutar');
  if (btnExec) {
    btnExec.addEventListener('click', () => {
      const msg = btnExec.dataset.confirm || '¿Ejecutar ahora?';
      if (!window.confirm(msg)) return;
      const url = btnExec.dataset.url;
      if (!url) return;

      btnExec.disabled = true;
      const txtOrig = btnExec.textContent;
      btnExec.textContent = '⚡ Ejecutando…';

      PM.post(url, {})
        .then((resp) => {
          if (resp && resp.ok) {
            const msg = `✅ ${resp.enviados} enviado(s) · ${resp.errores} error(es) · ${resp.skip} omitido(s)`;
            mostrarToast(msg, resp.errores > 0 ? 'warn' : 'ok');
            refrescarLog();
          } else {
            mostrarToast('Error: ' + ((resp && resp.error) || 'desconocido'), 'err');
          }
        })
        .catch((e) => mostrarToast('Error de red: ' + e.message, 'err'))
        .finally(() => {
          btnExec.disabled = false;
          btnExec.textContent = txtOrig;
        });
    });
  }

  // ─── Toast simple (sin depender de toast-area si no existe) ───────────────
  function mostrarToast(mensaje, tipo) {
    const colores = {
      ok:   'bg-primary text-white',
      warn: 'bg-amber-500 text-white',
      err:  'bg-red-600 text-white',
    };
    const t = document.createElement('div');
    t.className = `fixed bottom-6 right-6 z-[200] px-4 py-3 rounded-lg shadow-modal text-sm font-semibold ${colores[tipo] || colores.ok}`;
    t.textContent = mensaje;
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; }, 4500);
    setTimeout(() => t.remove(), 5000);
  }

  // ─── Auto-refresh del log de envíos cada 6 segundos ───────────────────────
  const panel = document.getElementById('envios-panel');
  if (!panel) return;
  const url = panel.dataset.url;
  if (!url) return;

  const elTotal = document.getElementById('stat-total');
  const elOk = document.getElementById('stat-ok');
  const elErr = document.getElementById('stat-err');
  const elLog = document.getElementById('envios-log');
  const elProxima = document.getElementById('proxima-fecha');

  function fmtFecha(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return `${dd}/${mm} ${hh}:${mi}`;
  }

  function fmtFechaLarga(iso) {
    if (!iso) return '—';
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString('es-CL', {
      weekday: 'long', day: 'numeric', month: 'long',
    });
  }

  function escape(s) {
    return String(s || '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  async function refrescarLog() {
    try {
      const r = await fetch(url + '?limit=100', { credentials: 'same-origin' });
      if (!r.ok) return;
      const data = await r.json();
      if (!data.ok) return;

      if (elTotal) elTotal.textContent = data.total;
      if (elOk)    elOk.textContent    = data.ok_count;
      if (elErr)   elErr.textContent   = data.err_count;

      if (elProxima && data.proxima_fecha) {
        elProxima.textContent = fmtFechaLarga(data.proxima_fecha) + ' a las ' + (data.hora_envio || '');
      } else if (elProxima && !data.proxima_fecha) {
        elProxima.textContent = '— (sin programación)';
      }

      if (!elLog) return;
      if (!data.envios || data.envios.length === 0) {
        elLog.innerHTML = '<div class="text-center text-xs text-gray-soft py-4">Sin envíos aún.</div>';
        return;
      }
      const rows = data.envios.map((e) => {
        const badge = e.estado === 'ok'
          ? '<span class="inline-block bg-primary-light text-primary-dark text-[10px] font-bold px-1.5 py-0.5 rounded">OK</span>'
          : `<span class="inline-block bg-red-100 text-red-700 text-[10px] font-bold px-1.5 py-0.5 rounded" title="${escape(e.error_msg)}">ERR</span>`;
        return `<tr class="hover:bg-off-white border-b border-border last:border-0">
          <td class="px-2 py-1.5 text-gray-soft whitespace-nowrap">${fmtFecha(e.enviado_en)}</td>
          <td class="px-2 py-1.5 text-gray-dark truncate max-w-[180px]" title="${escape(e.email)}">${escape(e.email)}</td>
          <td class="px-2 py-1.5 text-right">${badge}</td>
        </tr>`;
      }).join('');
      elLog.innerHTML = '<table class="w-full text-xs"><tbody>' + rows + '</tbody></table>';
    } catch (e) {
      // silencioso — la próxima iteración lo reintenta
    }
  }

  // Refrescá cada 6s mientras la pestaña esté visible
  let timer = null;
  function start() {
    if (timer) return;
    timer = setInterval(refrescarLog, 6000);
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop(); else { refrescarLog(); start(); }
  });
  start();
})();
