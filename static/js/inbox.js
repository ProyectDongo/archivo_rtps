/* ==========================================================================
   Inbox: lista a pantalla completa + atajos de teclado.
   El click en una fila navega a /intranet/correo/N/ (link <a> nativo).
   Depende de: portal_helpers.js (PM.post)
   ========================================================================== */

(function () {
  'use strict';

  // ─── Pintar avatares deterministas ──────────────────────────────────────
  function pintarAvatares(scope) {
    (scope || document).querySelectorAll('.avatar[data-color]').forEach(function (el) {
      el.style.backgroundColor = el.dataset.color;
    });
  }
  pintarAvatares();

  // ─── Pintar mini chips de etiqueta con su color ─────────────────────────
  (function () {
    document.querySelectorAll(
      '.tag-chip-mini[data-color], .tag-chip[data-color], .filter-tag[data-color], .active-chip-tag[data-color]'
    ).forEach(function (el) {
      const color = el.dataset.color;
      if (el.classList.contains('filter-tag') || el.classList.contains('active-chip-tag')) {
        el.style.color = color;
        el.style.borderColor = color;
      } else {
        el.style.backgroundColor = color;
      }
    });
  })();

  // ─── Barras del chart de stats ──────────────────────────────────────────
  document.querySelectorAll('.chart-bar[data-h]').forEach(function (el) {
    el.style.height = el.dataset.h + '%';
  });

  // ─── Top remitentes: click filtra ───────────────────────────────────────
  document.querySelectorAll('.sender-chip[data-remitente]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      window.location.href = '?q=' + encodeURIComponent(chip.dataset.remitente);
    });
  });

  // ─── Lista ──────────────────────────────────────────────────────────────
  const lista = document.getElementById('split-list');
  if (!lista) return;

  // ─── Toggle estrella en una fila (sin navegar) ──────────────────────────
  function toggleStarRow(correoId, rowEl) {
    PM.post('/intranet/correo/' + correoId + '/destacar/').then(function (data) {
      const svg = rowEl.querySelector('.row-star svg');
      if (svg) svg.setAttribute('fill', data.destacado ? 'currentColor' : 'none');
      rowEl.classList.toggle('is-starred', data.destacado);
    }).catch(function () { /* silencio: el usuario reintentará */ });
  }

  // ─── Click en fila ──────────────────────────────────────────────────────
  // Las filas ahora son <a href="/intranet/correo/N/"> — el click navega
  // automáticamente. Solo interceptamos clicks en .row-star y .row-check
  // para que NO sigan el link.
  lista.addEventListener('click', function (e) {
    const star = e.target.closest('.row-star');
    if (star) {
      e.preventDefault();
      e.stopPropagation();
      const row = star.closest('.correo-row');
      toggleStarRow(star.dataset.correoId, row);
      return;
    }
    // El checkbox para multi-select ya tiene su propio handler en bulk_select.js
    // que llama stopPropagation; acá solo nos aseguramos que no se confunda
    // con un click que dispara la navegación del <a>.
    if (e.target.closest('.row-check')) return;
  });

  // ─── Atajos de teclado ──────────────────────────────────────────────────
  // j/k: marca la fila siguiente/anterior con .keyboard-focus (highlight visual)
  // Enter: navega a la fila marcada (o la primera si no hay ninguna)
  // s: estrella sobre la fila marcada
  function todasFilas() { return Array.from(lista.querySelectorAll('.correo-row')); }
  function filaMarcada() { return lista.querySelector('.correo-row.keyboard-focus'); }

  function marcarFila(row) {
    lista.querySelectorAll('.correo-row.keyboard-focus').forEach(function (r) {
      r.classList.remove('keyboard-focus');
    });
    if (row) {
      row.classList.add('keyboard-focus');
      row.scrollIntoView({ block: 'nearest' });
    }
  }

  document.addEventListener('keydown', function (e) {
    const t = e.target;
    const isInput = t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable;
    if (isInput) return;
    // Si hay un compose flotante o modal abierto, no manejamos atajos.
    const fab = document.getElementById('compose-fab');
    if (fab && !fab.hidden) return;

    const filas = todasFilas();
    if (!filas.length) return;
    let idx = filas.indexOf(filaMarcada());

    if (e.key === 'j') {
      e.preventDefault();
      idx = (idx + 1) % filas.length;
      marcarFila(filas[idx]);
    } else if (e.key === 'k') {
      e.preventDefault();
      idx = idx <= 0 ? filas.length - 1 : idx - 1;
      marcarFila(filas[idx]);
    } else if (e.key === 'Enter') {
      const target = filas[idx >= 0 ? idx : 0];
      if (target && target.href) {
        e.preventDefault();
        window.location.href = target.href;
      }
    } else if (e.key === 's' && idx >= 0) {
      e.preventDefault();
      toggleStarRow(filas[idx].dataset.correoId, filas[idx]);
    }
  });

  // ─── Crear etiqueta nueva (diálogo en sidebar) ─────────────────────────
  const btnNew = document.getElementById('btn-new-tag');
  const dlg = document.getElementById('new-tag-dialog');
  const colorBtns = document.getElementById('new-tag-colors');
  const nameInput = document.getElementById('new-tag-name');
  const createBtn = document.getElementById('new-tag-create');
  const cancelBtn = document.getElementById('new-tag-cancel');

  if (btnNew && dlg) {
    let colorElegido = '#C80C0F';
    btnNew.addEventListener('click', function () {
      dlg.hidden = !dlg.hidden;
      if (!dlg.hidden) nameInput.focus();
    });
    cancelBtn.addEventListener('click', function () {
      dlg.hidden = true;
      nameInput.value = '';
    });
    colorBtns.querySelectorAll('button[data-color]').forEach(function (b) {
      b.addEventListener('click', function () {
        colorBtns.querySelectorAll('button').forEach(function (x) { x.classList.remove('selected'); });
        b.classList.add('selected');
        colorElegido = b.dataset.color;
      });
    });
    colorBtns.querySelector('button').classList.add('selected');

    createBtn.addEventListener('click', function () {
      const nombre = nameInput.value.trim();
      if (!nombre) { nameInput.focus(); return; }
      PM.post('/intranet/buzon/etiqueta-nueva/', {
        nombre: nombre,
        color: colorElegido,
      }).then(function () {
        window.location.reload();
      });
    });

    nameInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); createBtn.click(); }
    });
  }

  // ─── Popup de ayuda de operadores de búsqueda ──────────────────────────
  const helpBtn = document.getElementById('search-help-btn');
  const helpPop = document.getElementById('search-help-pop');
  if (helpBtn && helpPop) {
    helpBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      const abrir = helpPop.hidden;
      helpPop.hidden = !abrir;
      helpBtn.setAttribute('aria-expanded', String(abrir));
    });
    document.addEventListener('click', function (e) {
      if (!helpPop.hidden && !helpPop.contains(e.target) && e.target !== helpBtn) {
        helpPop.hidden = true;
        helpBtn.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !helpPop.hidden) {
        helpPop.hidden = true;
        helpBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }
})();


/* ════════════════════════════════════════════════════════════════════════
   Inbox UI: sidebar drawer (mobile), stats panel, advanced filters,
   buzones colapsable, tag-dot painting.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ─── Sidebar drawer (mobile) ────────────────────────────────────────────
  const sidebar       = document.getElementById('inbox-sidebar');
  const sidebarToggle = document.getElementById('sidebar-toggle');
  const sidebarClose  = document.getElementById('sidebar-close');
  const backdrop      = document.getElementById('sidebar-backdrop');

  function openSidebar() {
    if (!sidebar) return;
    sidebar.classList.add('open');
    if (backdrop) backdrop.hidden = false;
    document.body.style.overflow = 'hidden';
  }
  function closeSidebar() {
    if (!sidebar) return;
    sidebar.classList.remove('open');
    if (backdrop) backdrop.hidden = true;
    document.body.style.overflow = '';
  }
  if (sidebarToggle) sidebarToggle.addEventListener('click', openSidebar);
  if (sidebarClose)  sidebarClose.addEventListener('click', closeSidebar);
  if (backdrop)      backdrop.addEventListener('click', closeSidebar);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && sidebar && sidebar.classList.contains('open')) {
      closeSidebar();
    }
  });

  let lastIsDesktop = window.matchMedia('(min-width: 1024px)').matches;
  window.addEventListener('resize', function () {
    const isDesktop = window.matchMedia('(min-width: 1024px)').matches;
    if (isDesktop !== lastIsDesktop) {
      closeSidebar();
      lastIsDesktop = isDesktop;
    }
  });

  // ─── Stats panel toggle ─────────────────────────────────────────────────
  const statsToggle = document.getElementById('stats-toggle');
  const statsPanel  = document.getElementById('stats-panel');
  if (statsToggle && statsPanel) {
    const STORAGE_KEY = 'pm:inbox:stats_open';
    let preferAbierto = false;
    try { preferAbierto = localStorage.getItem(STORAGE_KEY) === '1'; } catch (e) {}
    if (preferAbierto) {
      statsPanel.hidden = false;
      statsToggle.setAttribute('aria-expanded', 'true');
    }
    statsToggle.addEventListener('click', function () {
      const abrir = statsPanel.hidden;
      statsPanel.hidden = !abrir;
      statsToggle.setAttribute('aria-expanded', String(abrir));
      try { localStorage.setItem(STORAGE_KEY, abrir ? '1' : '0'); } catch (e) {}
    });
  }

  // ─── Filtros avanzados toggle ───────────────────────────────────────────
  const advToggle = document.getElementById('adv-toggle');
  const advForm   = document.getElementById('toolbar-adv');
  if (advToggle && advForm) {
    const desdeIn = advForm.querySelector('input[name="desde"]');
    const hastaIn = advForm.querySelector('input[name="hasta"]');
    const tieneFiltrosFecha = (desdeIn && desdeIn.value) || (hastaIn && hastaIn.value);
    if (tieneFiltrosFecha) {
      advForm.hidden = false;
      advToggle.setAttribute('aria-expanded', 'true');
    }
    advToggle.addEventListener('click', function () {
      const abrir = advForm.hidden;
      advForm.hidden = !abrir;
      advToggle.setAttribute('aria-expanded', String(abrir));
    });
  }

  // ─── Pintar tag-dot del sidebar con su data-color ──────────────────────
  document.querySelectorAll('.sidebar-tag[data-color]').forEach(function (link) {
    const color = link.dataset.color;
    const dot = link.querySelector('.tag-dot');
    if (color && dot) dot.style.backgroundColor = color;
  });

  // ─── Buzones colapsable (sidebar) ──────────────────────────────────────
  const buzonList = document.getElementById('sidebar-buzon-list');
  const buzonsToggle = document.getElementById('sidebar-buzones-toggle');
  if (buzonList && buzonsToggle) {
    const STORAGE_KEY = 'pm:sidebar:buzones_expanded';

    let expandido = false;
    let hayPreferencia = false;
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      if (v !== null) { hayPreferencia = true; expandido = (v === '1'); }
    } catch (e) {}

    if (!hayPreferencia) {
      const activeOverflow = buzonList.querySelector(
        '.sidebar-buzon-overflow .sidebar-buzon.active'
      );
      if (activeOverflow) expandido = true;
    }

    if (expandido) {
      buzonList.classList.add('expanded');
      buzonsToggle.setAttribute('aria-expanded', 'true');
    }

    buzonsToggle.addEventListener('click', function () {
      const ahoraExpandido = !buzonList.classList.contains('expanded');
      buzonList.classList.toggle('expanded', ahoraExpandido);
      buzonsToggle.setAttribute('aria-expanded', String(ahoraExpandido));
      try { localStorage.setItem(STORAGE_KEY, ahoraExpandido ? '1' : '0'); } catch (e) {}
    });
  }
})();
