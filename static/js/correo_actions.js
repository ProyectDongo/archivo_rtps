/* ==========================================================================
   Acciones del correo (star, etiquetas, notas, snooze, hilo, marcar no-leído).
   Operan sobre cualquier .preview-card[data-correo-id] presente en el DOM.
   Pensado para correr en /intranet/correo/N/ (página de detalle).
   Depende de PM.post / PM.debounce (portal_helpers.js).
   ========================================================================== */
(function () {
  'use strict';

  function init() {
    const card = document.querySelector('.preview-card[data-correo-id]');
    if (!card) return;
    const cid = card.getAttribute('data-correo-id');

    // ─── Estrella prominente ────────────────────────────────────────────
    const star = card.querySelector('.preview-star');
    if (star) {
      star.addEventListener('click', function () {
        PM.post('/intranet/correo/' + cid + '/destacar/').then(function (data) {
          const svg = star.querySelector('svg');
          if (svg) svg.setAttribute('fill', data.destacado ? 'currentColor' : 'none');
          star.classList.toggle('is-active', data.destacado);
        }).catch(function () { /* silencio */ });
      });
    }

    // ─── Quitar etiqueta (botón × en cada chip) ─────────────────────────
    function wireRemoveBtn(btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        const chip = btn.closest('.tag-chip');
        const tagId = btn.dataset.tagId;
        PM.post('/intranet/correo/' + cid + '/etiqueta/', {
          etiqueta_id: tagId,
          accion: 'quitar',
        }).then(function () {
          chip.remove();
          const wrap = card.querySelector('.preview-tags-list');
          if (wrap && !wrap.querySelector('.tag-chip')) {
            const empty = document.createElement('span');
            empty.className = 'preview-tags-empty';
            empty.textContent = 'Sin etiquetas';
            wrap.insertBefore(empty, wrap.querySelector('.tag-add-btn'));
          }
        });
      });
    }
    card.querySelectorAll('.tag-remove').forEach(wireRemoveBtn);

    // ─── Botón "+ Asignar" → muestra el picker ──────────────────────────
    const addBtn = card.querySelector('#tag-add-btn');
    const picker = card.querySelector('#tag-picker');
    if (addBtn && picker) {
      let etiquetasBuzon = [];
      const dataEl = document.getElementById('etiquetas-disponibles-data');
      if (dataEl) {
        try { etiquetasBuzon = JSON.parse(dataEl.textContent); } catch (e) { etiquetasBuzon = []; }
      }

      addBtn.addEventListener('click', function () {
        if (!picker.hidden) {
          picker.hidden = true;
          return;
        }
        const yaAsignadas = new Set();
        card.querySelectorAll('.tag-chip[data-tag-id]').forEach(function (el) {
          yaAsignadas.add(el.dataset.tagId);
        });
        picker.innerHTML = '';
        const disponibles = etiquetasBuzon.filter(function (et) {
          return !yaAsignadas.has(String(et.id));
        });
        if (disponibles.length === 0) {
          const empty = document.createElement('span');
          empty.className = 'tag-picker-empty';
          empty.textContent = 'Todas asignadas. Crea una nueva en la barra de filtros.';
          picker.appendChild(empty);
        } else {
          disponibles.forEach(function (et) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'tag-chip';
            b.style.backgroundColor = et.color;
            b.style.cursor = 'pointer';
            b.style.border = 'none';
            b.dataset.tagId = et.id;
            b.innerHTML = '<span class="tag-dot"></span>' + et.nombre;
            b.addEventListener('click', function () {
              PM.post('/intranet/correo/' + cid + '/etiqueta/', {
                etiqueta_id: et.id,
                accion: 'asignar',
              }).then(function (data) {
                const wrap = card.querySelector('.preview-tags-list');
                const empty = wrap.querySelector('.preview-tags-empty');
                if (empty) empty.remove();
                const chip = document.createElement('span');
                chip.className = 'tag-chip';
                chip.dataset.tagId = data.etiqueta.id;
                chip.dataset.color = data.etiqueta.color;
                chip.style.backgroundColor = data.etiqueta.color;
                chip.innerHTML = '<span class="tag-dot"></span>' + data.etiqueta.nombre +
                  ' <button type="button" class="tag-remove" data-tag-id="' + data.etiqueta.id + '" aria-label="Quitar etiqueta">×</button>';
                wrap.insertBefore(chip, addBtn);
                wireRemoveBtn(chip.querySelector('.tag-remove'));
                picker.hidden = true;
              });
            });
            picker.appendChild(b);
          });
        }
        picker.hidden = false;
      });
    }

    // ─── Marcar como no leído → vuelve a la bandeja ─────────────────────
    const unreadBtn = card.querySelector('.preview-unread-btn');
    if (unreadBtn) {
      unreadBtn.addEventListener('click', function () {
        PM.post('/intranet/correo/' + cid + '/leido/').then(function (data) {
          if (!data.is_leido) {
            window.location.href = '/intranet/bandeja/';
          }
        });
      });
    }

    // ─── Snooze: dropdown con presets + custom ──────────────────────────
    const snzBtn = card.querySelector('.preview-snooze-btn');
    const snzMenu = card.querySelector('.preview-snooze-menu');
    if (snzBtn && snzMenu) {
      snzBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        snzMenu.hidden = !snzMenu.hidden;
      });
      document.addEventListener('click', function (e) {
        if (!snzMenu.hidden && !snzMenu.contains(e.target) && e.target !== snzBtn) {
          snzMenu.hidden = true;
        }
      });
      snzMenu.querySelectorAll('.snooze-opt[data-preset]').forEach(function (opt) {
        opt.addEventListener('click', function () {
          PM.post('/intranet/correo/' + cid + '/snooze/', { preset: opt.dataset.preset })
            .then(function () { window.location.href = '/intranet/bandeja/'; });
        });
      });
      const customGo = snzMenu.querySelector('.snooze-custom-go');
      const customInput = snzMenu.querySelector('.snooze-custom-input');
      if (customGo && customInput) {
        customGo.addEventListener('click', function () {
          const v = customInput.value;
          if (!v) { customInput.focus(); return; }
          PM.post('/intranet/correo/' + cid + '/snooze/', { until: v })
            .then(function () { window.location.href = '/intranet/bandeja/'; })
            .catch(function () { alert('No se pudo posponer: fecha inválida'); });
        });
      }
      const cancelBtn = snzMenu.querySelector('.snooze-cancel');
      if (cancelBtn) {
        cancelBtn.addEventListener('click', function () {
          PM.post('/intranet/correo/' + cid + '/unsnooze/')
            .then(function () { window.location.reload(); });
        });
      }
    }

    // ─── Hilo: toggle expandible (los items ya son <a> que navegan solos) ─
    const thBtn = card.querySelector('.preview-thread-toggle');
    const thList = card.querySelector('.preview-thread-list');
    if (thBtn && thList) {
      thBtn.addEventListener('click', function () {
        const abrir = thList.hidden;
        thList.hidden = !abrir;
        thBtn.setAttribute('aria-expanded', String(abrir));
      });
    }

    // ─── Notas internas: autosave on blur + debounced input ─────────────
    const nota = card.querySelector('.preview-notas-input');
    const status = card.querySelector('#notas-status');
    if (nota) {
      const guardar = function () {
        if (status) { status.textContent = 'Guardando…'; status.className = 'notas-status saving'; }
        PM.post('/intranet/correo/' + cid + '/notas/', { notas: nota.value })
          .then(function () {
            if (status) { status.textContent = 'Guardado ✓'; status.className = 'notas-status saved'; }
            setTimeout(function () { if (status) status.textContent = ''; }, 2000);
          })
          .catch(function () {
            if (status) { status.textContent = 'Error al guardar'; status.className = 'notas-status'; }
          });
      };
      nota.addEventListener('blur', guardar);
      nota.addEventListener('input', PM.debounce(guardar, 1500));
    }

    // ─── Pintar avatares y tag chips ────────────────────────────────────
    card.querySelectorAll('.avatar[data-color]').forEach(function (el) {
      el.style.backgroundColor = el.dataset.color;
    });
    card.querySelectorAll('.tag-chip-mini[data-color], .tag-chip[data-color]').forEach(function (el) {
      el.style.backgroundColor = el.dataset.color;
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
