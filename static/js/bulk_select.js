// Multi-select + bulk actions en el inbox.
// Depende de PM.post (portal_helpers.js).
// Estado: Set<correoId> + último índice clickeado para Shift+click.
(function () {
  'use strict';

  const lista = document.getElementById('split-list');
  const bar = document.getElementById('bulk-bar');
  if (!lista || !bar) return;

  const checkAll = document.getElementById('bulk-checkall');
  const counterN = document.getElementById('bulk-bar-n');
  const counterS = document.getElementById('bulk-bar-s');
  const closeBtn = document.getElementById('bulk-bar-close');

  const seleccion = new Set();
  let lastIdx = -1;

  function todosCheckboxes() {
    return Array.from(lista.querySelectorAll('.row-check'));
  }

  function pintar() {
    const n = seleccion.size;
    if (n === 0) {
      bar.hidden = true;
      if (checkAll) { checkAll.checked = false; checkAll.indeterminate = false; }
      return;
    }
    bar.hidden = false;
    counterN.textContent = String(n);
    counterS.textContent = n === 1 ? '' : 's';

    // Indeterminate state del check-all
    const total = todosCheckboxes().length;
    if (checkAll) {
      checkAll.checked = (n === total);
      checkAll.indeterminate = (n > 0 && n < total);
    }
  }

  function toggleId(id, marcar) {
    if (marcar) seleccion.add(id);
    else seleccion.delete(id);
    const cb = lista.querySelector('.row-check[data-correo-id="' + CSS.escape(id) + '"]');
    if (cb) {
      cb.checked = marcar;
      const row = cb.closest('.correo-row');
      if (row) row.classList.toggle('is-selected', marcar);
    }
  }

  function clearAll() {
    Array.from(seleccion).forEach(id => toggleId(id, false));
    lastIdx = -1;
    pintar();
  }

  // Click en checkbox de fila — con soporte Shift+click para rango.
  lista.addEventListener('click', function (e) {
    const cb = e.target.closest('.row-check');
    if (!cb) return;
    e.stopPropagation();   // no abrir preview
    const all = todosCheckboxes();
    const idx = all.indexOf(cb);
    const id = cb.dataset.correoId;
    const marcar = cb.checked;

    if (e.shiftKey && lastIdx >= 0 && idx >= 0) {
      const [a, b] = idx < lastIdx ? [idx, lastIdx] : [lastIdx, idx];
      for (let i = a; i <= b; i++) {
        const ic = all[i];
        toggleId(ic.dataset.correoId, marcar);
      }
    } else {
      toggleId(id, marcar);
    }
    lastIdx = idx;
    pintar();
  });

  // Click en cualquier parte de la fila NO debe propagar al preview cuando hay
  // selección activa Y no fue el checkbox. Pero queremos preservar UX original
  // (click abre preview). Solución: solo trackear el click del checkbox.

  // Check-all toggle: marca/desmarca todos en la página actual.
  if (checkAll) {
    checkAll.addEventListener('change', function () {
      const all = todosCheckboxes();
      if (checkAll.checked) {
        all.forEach(cb => toggleId(cb.dataset.correoId, true));
      } else {
        clearAll();
      }
      pintar();
    });
  }

  closeBtn.addEventListener('click', clearAll);

  // ESC limpia selección.
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && seleccion.size > 0) {
      const t = e.target;
      const isInput = t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable;
      if (isInput) return;
      // No interferir con cierres de drawer/lightbox: solo si esos están cerrados.
      const lb = document.getElementById('lightbox');
      const sb = document.getElementById('inbox-sidebar');
      if (lb && !lb.hidden) return;
      if (sb && sb.classList.contains('open')) return;
      clearAll();
    }
  });

  // Acciones bulk.
  function ejecutarAccion(accion, etiquetaId) {
    if (seleccion.size === 0) return;
    const ids = Array.from(seleccion).join(',');
    const data = { ids: ids, accion: accion };
    if (etiquetaId) data.etiqueta_id = etiquetaId;
    return PM.post('/intranet/correos/bulk/', data).then(function (resp) {
      if (!resp || !resp.ok) return;
      // Reflejar cambios en UI sin recargar.
      seleccion.forEach(function (id) {
        const row = lista.querySelector('.correo-row[data-correo-id="' + CSS.escape(id) + '"]');
        if (!row) return;
        if (accion === 'leer') row.classList.add('is-read');
        else if (accion === 'no_leer') row.classList.remove('is-read');
        else if (accion === 'destacar') {
          row.classList.add('is-starred');
          const svg = row.querySelector('.row-star svg');
          if (svg) svg.setAttribute('fill', 'currentColor');
        } else if (accion === 'no_destacar') {
          row.classList.remove('is-starred');
          const svg = row.querySelector('.row-star svg');
          if (svg) svg.setAttribute('fill', 'none');
        }
      });
      // Para etiquetas el cambio visual en la lista es complejo (chips); más
      // simple recargar para reflejar — pero solo si el filtro actual incluye
      // etiqueta. En la mayoría de casos no es crítico. Mantenemos la lista
      // tal cual y mostramos toast.
      if (accion === 'asignar_etiqueta' || accion === 'quitar_etiqueta') {
        // Forzar recarga para reflejar etiquetas en chips de la lista.
        window.location.reload();
        return;
      }
      // Actualizar badge si vino en la respuesta.
      if (typeof resp.no_leidos_buzon === 'number') {
        const item = document.querySelector('.sidebar-buzon.active, .buzon-tab.active');
        if (item) {
          let badge = item.querySelector('.sidebar-badge, .buzon-tab-badge');
          if (resp.no_leidos_buzon > 0) {
            if (!badge) {
              badge = document.createElement('span');
              badge.className = item.classList.contains('sidebar-buzon') ? 'sidebar-badge' : 'buzon-tab-badge';
              item.appendChild(badge);
            }
            badge.textContent = String(resp.no_leidos_buzon);
          } else if (badge) {
            badge.remove();
          }
        }
      }
      clearAll();
    });
  }

  bar.querySelectorAll('[data-bulk-accion]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      ejecutarAccion(btn.getAttribute('data-bulk-accion'));
    });
  });

  // Menú de etiquetas
  const tagBtn = document.getElementById('bulk-tag-btn');
  const tagMenu = document.getElementById('bulk-tag-menu');
  if (tagBtn && tagMenu) {
    tagBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      tagMenu.hidden = !tagMenu.hidden;
    });
    tagMenu.querySelectorAll('.bulk-tag-opt').forEach(function (opt) {
      opt.addEventListener('click', function () {
        ejecutarAccion('asignar_etiqueta', opt.getAttribute('data-etiqueta-id'));
        tagMenu.hidden = true;
      });
    });
    document.addEventListener('click', function (e) {
      if (!tagMenu.hidden && !tagMenu.contains(e.target) && e.target !== tagBtn) {
        tagMenu.hidden = true;
      }
    });
  }
})();
