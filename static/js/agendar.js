/**
 * Agendar — interacciones del catálogo público.
 *
 *  - Tabs servicios / repuestos
 *  - Carrito: agregar / quitar items, total + duración estimada
 *  - Calendario: click en día → fetch /agendar/disponibilidad/?fecha=...
 *  - Slots: click en slot → marca como elegido + habilita botón confirmar
 *
 * Anti-bot consciente: el form trae captcha Fernet + Turnstile + honeypot
 * server-side. Acá solo manejamos UX.
 */
(function() {
  'use strict';

  const CATALOGO = JSON.parse(document.getElementById('catalogo-data').textContent);
  const cart = new Map();   // id → {item, qty=1}

  const els = {
    tabs:        document.querySelectorAll('.cat-tab'),
    panels:      {
      servicio: document.getElementById('cat-servicio'),
      repuesto: document.getElementById('cat-repuesto'),
    },
    cartItems:   document.getElementById('cart-items'),
    cartTotals:  document.getElementById('cart-totals'),
    cartTotal:   document.getElementById('cart-total'),
    cartDur:     document.getElementById('cart-dur'),
    calendario:  document.getElementById('calendario'),
    slotsCont:   document.getElementById('slots-container'),
    slotsGrid:   document.getElementById('slots-grid'),
    hiddenFecha: document.getElementById('hidden-fecha'),
    hiddenHora:  document.getElementById('hidden-hora'),
    hiddenItems: document.getElementById('form-items-hidden'),
    btnConf:     document.getElementById('btn-confirmar'),
    form:        document.getElementById('agendar-form'),
  };

  // ─── Tabs ────────────────────────────────────────────────────────────
  els.tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const tipo = tab.dataset.tipo;
      els.tabs.forEach(t => {
        t.classList.toggle('active', t === tab);
        t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
      });
      Object.entries(els.panels).forEach(([t, panel]) => {
        if (panel) panel.classList.toggle('hidden', t !== tipo);
      });
    });
  });

  // ─── Carrito ─────────────────────────────────────────────────────────
  function agregarAlCarrito(id) {
    const item = CATALOGO.find(i => i.id === id);
    if (!item) return;
    if (cart.has(id)) cart.delete(id);
    else cart.set(id, item);
    renderCart();
  }

  function fmtCLP(n) {
    return '$' + (n || 0).toLocaleString('es-CL');
  }

  function fmtDur(min) {
    if (!min) return '—';
    if (min < 60) return min + ' min';
    const h = Math.floor(min / 60);
    const m = min % 60;
    return h + 'h' + (m ? ' ' + m + 'min' : '');
  }

  function renderCart() {
    if (cart.size === 0) {
      els.cartItems.innerHTML = '<p class="cart-empty">Aún no agregaste nada. Tocá el "+" en los items que necesités.</p>';
      els.cartTotals.hidden = true;
      els.hiddenItems.innerHTML = '';
    } else {
      let html = '';
      let total = 0, dur = 0;
      cart.forEach(it => {
        html += `<div class="cart-item">
          <span class="cart-item-name">${escapeHtml(it.nombre)}</span>
          <span class="cart-item-price">${fmtCLP(it.precio)}</span>
          <button type="button" class="cart-item-rm" data-rm-id="${it.id}" aria-label="Quitar">×</button>
        </div>`;
        total += it.precio || 0;
        dur += it.duracion || 0;
      });
      els.cartItems.innerHTML = html;
      els.cartTotal.textContent = fmtCLP(total);
      els.cartDur.textContent = fmtDur(dur) + ' aprox';
      els.cartTotals.hidden = false;

      // hidden inputs para el form
      els.hiddenItems.innerHTML = Array.from(cart.keys())
        .map(id => `<input type="hidden" name="item_ids[]" value="${id}">`).join('');
    }

    // sync visual de los "+"
    document.querySelectorAll('.cat-add-btn').forEach(btn => {
      const id = parseInt(btn.dataset.addId, 10);
      if (cart.has(id)) {
        btn.classList.add('is-added');
        btn.textContent = '✓';
      } else {
        btn.classList.remove('is-added');
        btn.textContent = '+';
      }
    });

    actualizarBtnConf();
  }

  document.addEventListener('click', (e) => {
    const add = e.target.closest('[data-add-id]');
    if (add) {
      e.preventDefault();
      agregarAlCarrito(parseInt(add.dataset.addId, 10));
      return;
    }
    const rm = e.target.closest('[data-rm-id]');
    if (rm) {
      e.preventDefault();
      cart.delete(parseInt(rm.dataset.rmId, 10));
      renderCart();
    }
  });

  // ─── Calendario + slots ──────────────────────────────────────────────
  let fechaElegida = null;
  let horaElegida  = null;

  els.calendario.addEventListener('click', async (e) => {
    const dia = e.target.closest('.cal-day:not([disabled])');
    if (!dia) return;

    fechaElegida = dia.dataset.fecha;
    horaElegida  = null;
    els.hiddenFecha.value = fechaElegida;
    els.hiddenHora.value  = '';

    document.querySelectorAll('.cal-day').forEach(d => d.classList.toggle('active', d === dia));
    await cargarSlots(fechaElegida);
    actualizarBtnConf();
  });

  async function cargarSlots(fecha) {
    els.slotsGrid.innerHTML = '<p style="color:#888;padding:1rem">Cargando...</p>';
    els.slotsCont.hidden = false;
    try {
      const resp = await fetch(`/agendar/disponibilidad/?fecha=${encodeURIComponent(fecha)}`);
      const data = await resp.json();
      if (!data.laboral) {
        els.slotsGrid.innerHTML = `<p style="color:#B71C1C;padding:1rem">${escapeHtml(data.motivo || 'Día no disponible.')}</p>`;
        return;
      }
      if (!data.slots || data.slots.length === 0) {
        els.slotsGrid.innerHTML = '<p style="color:#888;padding:1rem">No hay horarios disponibles en este día.</p>';
        return;
      }
      els.slotsGrid.innerHTML = data.slots.map(s =>
        `<button type="button" class="slot-btn" data-hora="${s.hora}" ${s.disponible ? '' : 'disabled'}>${s.hora}</button>`
      ).join('');
    } catch (err) {
      els.slotsGrid.innerHTML = '<p style="color:#B71C1C;padding:1rem">Error al cargar horarios. Recargá.</p>';
    }
  }

  els.slotsGrid.addEventListener('click', (e) => {
    const btn = e.target.closest('.slot-btn:not([disabled])');
    if (!btn) return;
    horaElegida = btn.dataset.hora;
    els.hiddenHora.value = horaElegida;
    document.querySelectorAll('.slot-btn').forEach(b => b.classList.toggle('active', b === btn));
    actualizarBtnConf();
  });

  // ─── Botón confirmar habilitado/deshabilitado ────────────────────────
  function actualizarBtnConf() {
    const ok = cart.size > 0 && fechaElegida && horaElegida;
    els.btnConf.disabled = !ok;
  }

  // ─── Util ────────────────────────────────────────────────────────────
  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );
  }
})();
