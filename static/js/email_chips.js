/* ==========================================================================
   Email chips widget (CSP-safe, sin dependencias).

   Convierte un <input type="text" data-emailchips> en una caja de chips
   estilo Gmail/Outlook:
     - Separadores que confirman un chip: coma, punto-y-coma, Enter, Tab, espacio
       (solo si el texto tiene formato de email).
     - Backspace en input vacío: borra el último chip.
     - Click en la "×" de un chip: lo borra.
     - blur: el texto pendiente se intenta confirmar como chip.
     - Pegar texto con varios emails: se splittea por , ; o \n.
     - Autocompletado opcional vía fetch a `data-suggest-url`.

   El widget mantiene un input hidden con el mismo `name` para que el form
   tradicional siga funcionando sin tocar el backend: emails coma-separados.

   Uso en template:
     <input type="text" name="to" value="" data-emailchips
            data-max="30" data-suggest-url="/intranet/contactos/">

   Inicialización: EmailChips.init(rootEl) — si no se pasa root, escanea body.
   ========================================================================== */
(function () {
  'use strict';

  const EMAIL_RE = /^[\w.+-]+@[\w-]+\.[\w.-]+$/;

  function isEmail(s) {
    return EMAIL_RE.test((s || '').trim());
  }

  function debounce(fn, ms) {
    let t;
    return function () {
      const args = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  }

  function createWidget(origInput) {
    if (origInput.dataset.emailchipsReady === '1') return;
    origInput.dataset.emailchipsReady = '1';

    const name = origInput.name || 'to';
    const max = parseInt(origInput.dataset.max || '30', 10);
    const suggestUrl = origInput.dataset.suggestUrl || '';
    const placeholder = origInput.placeholder || '';

    // Contenedor del widget
    const wrap = document.createElement('div');
    wrap.className = 'ec-wrap';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', origInput.getAttribute('aria-label') || name);

    // Input visible donde el usuario tipea
    const visible = document.createElement('input');
    visible.type = 'text';
    visible.className = 'ec-input';
    visible.autocomplete = 'off';
    visible.spellcheck = false;
    visible.placeholder = placeholder;
    if (origInput.id) visible.id = origInput.id;

    // Input hidden con el mismo `name` para el form submit
    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = name;

    // Contador "N de MAX"
    const counter = document.createElement('span');
    counter.className = 'ec-counter';

    // Dropdown de sugerencias
    const suggest = document.createElement('div');
    suggest.className = 'ec-suggest';
    suggest.hidden = true;
    suggest.setAttribute('role', 'listbox');

    wrap.appendChild(visible);
    wrap.appendChild(hidden);
    wrap.appendChild(counter);
    wrap.appendChild(suggest);

    // Reemplaza el input original por el wrap
    origInput.style.display = 'none';
    origInput.removeAttribute('required');
    origInput.parentNode.insertBefore(wrap, origInput);
    // Movemos el original adentro para preservar `value` inicial y removal
    wrap.appendChild(origInput);

    // ─── Estado ──────────────────────────────────────────────────────
    let chips = [];
    let suggIndex = -1;
    let suggItems = [];

    function syncHidden() {
      hidden.value = chips.join(', ');
      counter.textContent = chips.length + ' / ' + max;
      counter.classList.toggle('is-full', chips.length >= max);
      counter.classList.toggle('is-over', chips.length > max);
    }

    function addChip(email) {
      email = (email || '').trim().replace(/^[<"]+|[>"]+$/g, '').toLowerCase();
      if (!email) return false;
      // Si viene "Nombre <email>", extraer email
      const m = email.match(/<([\w.+-]+@[\w-]+\.[\w.-]+)>/);
      if (m) email = m[1].toLowerCase();
      if (!isEmail(email)) return false;
      if (chips.indexOf(email) !== -1) return false;
      if (chips.length >= max) return false;
      chips.push(email);
      renderChips();
      syncHidden();
      return true;
    }

    function removeChip(email) {
      chips = chips.filter(function (e) { return e !== email; });
      renderChips();
      syncHidden();
    }

    function renderChips() {
      // Borra chips actuales (todos los nodos .ec-chip) preservando input visible
      const old = wrap.querySelectorAll('.ec-chip');
      old.forEach(function (n) { n.remove(); });
      chips.forEach(function (email) {
        const chip = document.createElement('span');
        chip.className = 'ec-chip';
        const txt = document.createElement('span');
        txt.className = 'ec-chip-text';
        txt.textContent = email;
        const x = document.createElement('button');
        x.type = 'button';
        x.className = 'ec-chip-x';
        x.setAttribute('aria-label', 'Quitar ' + email);
        x.textContent = '×';
        x.addEventListener('click', function () { removeChip(email); visible.focus(); });
        chip.appendChild(txt);
        chip.appendChild(x);
        // Insertamos antes del input visible (chips a la izquierda)
        wrap.insertBefore(chip, visible);
      });
    }

    function commitPending() {
      const val = visible.value.trim().replace(/[,;]+$/, '');
      if (!val) return false;
      const ok = addChip(val);
      if (ok) {
        visible.value = '';
        hideSuggest();
      } else if (val.length > 0 && !isEmail(val)) {
        visible.classList.add('is-invalid');
        setTimeout(function () { visible.classList.remove('is-invalid'); }, 800);
      }
      return ok;
    }

    // ─── Eventos del input visible ────────────────────────────────────
    visible.addEventListener('keydown', function (e) {
      // Navegar dropdown
      if (!suggest.hidden && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        if (suggItems.length === 0) return;
        suggIndex += (e.key === 'ArrowDown' ? 1 : -1);
        if (suggIndex < 0) suggIndex = suggItems.length - 1;
        if (suggIndex >= suggItems.length) suggIndex = 0;
        highlightSuggest();
        return;
      }
      if (!suggest.hidden && e.key === 'Enter' && suggIndex >= 0) {
        e.preventDefault();
        pickSuggest(suggItems[suggIndex]);
        return;
      }
      if (e.key === 'Escape' && !suggest.hidden) {
        e.preventDefault();
        hideSuggest();
        return;
      }
      // Separadores que confirman
      if (e.key === ',' || e.key === ';' || e.key === 'Enter' || e.key === 'Tab') {
        const v = visible.value.trim();
        if (v) {
          e.preventDefault();
          commitPending();
        } else if (e.key === 'Enter') {
          // Enter con input vacío: dejar que el form submitee
          // (no preventDefault)
        }
        return;
      }
      // Backspace en input vacío: borrar último chip
      if (e.key === 'Backspace' && visible.value === '' && chips.length > 0) {
        e.preventDefault();
        removeChip(chips[chips.length - 1]);
        return;
      }
    });

    visible.addEventListener('input', debounce(function () {
      const q = visible.value.trim();
      if (q.length < 1 || !suggestUrl) {
        hideSuggest();
        return;
      }
      fetchSuggest(q);
    }, 250));

    visible.addEventListener('paste', function (e) {
      const text = (e.clipboardData || window.clipboardData).getData('text');
      if (!text) return;
      // Si el pegado contiene separadores, lo procesamos manualmente
      if (/[,;\n]/.test(text)) {
        e.preventDefault();
        text.split(/[,;\n]+/).forEach(function (piece) {
          piece = piece.trim();
          if (piece) addChip(piece);
        });
        visible.value = '';
      }
    });

    visible.addEventListener('blur', function () {
      // Pequeño delay para que un click en una sugerencia tenga tiempo de disparar
      setTimeout(function () {
        commitPending();
        hideSuggest();
      }, 150);
    });

    wrap.addEventListener('click', function (e) {
      // Click en zona vacía del wrap → focus al input visible
      if (e.target === wrap) visible.focus();
    });

    // ─── Sugerencias ─────────────────────────────────────────────────
    function fetchSuggest(q) {
      const url = suggestUrl + (suggestUrl.indexOf('?') === -1 ? '?' : '&') + 'q=' + encodeURIComponent(q);
      fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
        .then(function (r) { return r.ok ? r.json() : { contactos: [] }; })
        .then(function (data) {
          const items = (data.contactos || []).filter(function (c) {
            return chips.indexOf(c.email) === -1;
          });
          renderSuggest(items);
        })
        .catch(function () { /* silent */ });
    }

    function renderSuggest(items) {
      suggItems = items;
      suggIndex = -1;
      suggest.innerHTML = '';
      if (items.length === 0) {
        hideSuggest();
        return;
      }
      items.forEach(function (it, idx) {
        const row = document.createElement('div');
        row.className = 'ec-suggest-row';
        row.setAttribute('role', 'option');
        row.dataset.idx = String(idx);
        const nombre = document.createElement('span');
        nombre.className = 'ec-suggest-name';
        nombre.textContent = it.nombre || it.email.split('@')[0];
        const email = document.createElement('span');
        email.className = 'ec-suggest-email';
        email.textContent = it.email;
        row.appendChild(nombre);
        row.appendChild(email);
        row.addEventListener('mousedown', function (e) {
          // mousedown (no click) para que dispare antes del blur del input
          e.preventDefault();
          pickSuggest(it);
        });
        suggest.appendChild(row);
      });
      suggest.hidden = false;
    }

    function highlightSuggest() {
      const rows = suggest.querySelectorAll('.ec-suggest-row');
      rows.forEach(function (r, i) {
        r.classList.toggle('is-active', i === suggIndex);
      });
    }

    function pickSuggest(item) {
      if (!item) return;
      addChip(item.email);
      visible.value = '';
      hideSuggest();
      visible.focus();
    }

    function hideSuggest() {
      suggest.hidden = true;
      suggItems = [];
      suggIndex = -1;
    }

    // ─── API expuesta en el input original (hidden tras la transformación) ──
    function setValue(csv) {
      chips = [];
      renderChips();
      (csv || '').split(/[,;]+/).forEach(function (piece) {
        piece = piece.trim();
        if (piece) addChip(piece);
      });
      visible.value = '';
      hideSuggest();
    }
    function getValue() { return chips.join(', '); }
    function clear() { setValue(''); }
    function getVisible() { return visible; }

    origInput._chipsApi = {
      setValue: setValue,
      getValue: getValue,
      clear: clear,
      getVisible: getVisible,
      getChips: function () { return chips.slice(); },
    };

    // ─── Inicialización: parsear value preexistente del input original ──
    const initial = (origInput.value || '').trim();
    if (initial) {
      initial.split(/[,;]+/).forEach(function (piece) {
        piece = piece.trim();
        if (piece) addChip(piece);
      });
    }
    syncHidden();
  }

  function init(root) {
    const scope = root || document;
    scope.querySelectorAll('input[data-emailchips]').forEach(createWidget);
  }

  // Helper de conveniencia: obtiene la API a partir del input (tanto hidden
  // post-transform como visible). Devuelve null si no es un chips widget.
  function api(input) {
    if (!input) return null;
    if (input._chipsApi) return input._chipsApi;
    // Si pasaron el visible, subir al wrap y bajar al hidden
    const wrap = input.closest && input.closest('.ec-wrap');
    if (!wrap) return null;
    const hidden = wrap.querySelector('input[type="hidden"]');
    return hidden && hidden._chipsApi ? hidden._chipsApi : null;
  }

  window.EmailChips = { init: init, isEmail: isEmail, api: api };

  document.addEventListener('DOMContentLoaded', function () { init(document); });
})();
