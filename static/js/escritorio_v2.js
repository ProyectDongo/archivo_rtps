(function () {
  'use strict';

  // ── Clock ────────────────────────────────────────────────────────
  function pad(n) { return String(n).padStart(2, '0'); }
  function tick() {
    var el = document.getElementById('ev2-clock');
    if (!el) return;
    var n = new Date();
    el.textContent = pad(n.getHours()) + ':' + pad(n.getMinutes());
  }
  tick();
  setInterval(tick, 10000);

  // ── Time-of-day greeting ─────────────────────────────────────────
  var grEl = document.getElementById('ev2-greeting-text');
  if (grEl) {
    var strong = grEl.querySelector('strong');
    var h = new Date().getHours();
    var pfx = h < 12 ? 'Buenos días, ' : h < 19 ? 'Buenas tardes, ' : 'Buenas noches, ';
    grEl.textContent = '';
    grEl.appendChild(document.createTextNode(pfx));
    if (strong) grEl.appendChild(strong);
  }

  // ── Colapsar ─────────────────────────────────────────────────────
  function colapsarTodos() {
    document.querySelectorAll('.ev2-tile.open').forEach(function (t) {
      t.classList.remove('open');
    });
  }
  var btnC = document.getElementById('ev2-btn-colapsar');
  if (btnC) btnC.addEventListener('click', colapsarTodos);

  // ── Draggable tiles + accordion toggle ───────────────────────────
  var TILE_W   = 405;
  var GAP      = 14;
  var COLS     = 2;
  var PAD_L    = 32;
  var PAD_T    = 8;

  function defaultPos(i) {
    return {
      left: PAD_L + (i % COLS) * (TILE_W + GAP),
      top:  PAD_T + Math.floor(i / COLS) * (72 + GAP)
    };
  }

  function loadPositions() {
    try { return JSON.parse(localStorage.getItem('ev2-tile-pos') || 'null'); }
    catch (e) { return null; }
  }

  function savePositions() {
    var data = [];
    document.querySelectorAll('.ev2-tile').forEach(function (t) {
      data.push({ left: t.offsetLeft, top: t.offsetTop });
    });
    try { localStorage.setItem('ev2-tile-pos', JSON.stringify(data)); } catch (e) {}
  }

  var saved   = loadPositions();
  var wasDrag = false; // flag: last mousedown ended as a drag, skip click

  document.querySelectorAll('.ev2-tile').forEach(function (tile, i) {

    // ── Set initial position ──────────────────────────────────────
    var pos = (saved && saved[i]) ? saved[i] : defaultPos(i);
    tile.style.left = pos.left + 'px';
    tile.style.top  = pos.top  + 'px';

    var head = tile.querySelector('.ev2-tile-head');
    if (!head) return;

    // ── Accordion toggle (click) ──────────────────────────────────
    // Only fires if mouseup was NOT a drag
    head.addEventListener('click', function (e) {
      if (wasDrag) { wasDrag = false; return; }
      e.stopPropagation();
      var wasOpen = tile.classList.contains('open');
      colapsarTodos();
      if (!wasOpen) tile.classList.add('open');
    });

    // ── Drag (mousedown → mousemove → mouseup) ────────────────────
    head.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      // let links/buttons handle their own click
      if (e.target.closest('a, button')) return;

      e.preventDefault();

      var sx = e.clientX, sy = e.clientY;
      var sl = tile.offsetLeft, st = tile.offsetTop;
      var moved = false;

      // bring this tile above others
      document.querySelectorAll('.ev2-tile').forEach(function (t) { t.style.zIndex = '10'; });
      tile.style.zIndex = '50';

      function onMove(ev) {
        var dx = ev.clientX - sx;
        var dy = ev.clientY - sy;
        if (!moved && (Math.abs(dx) > 5 || Math.abs(dy) > 5)) {
          moved = true;
          tile.classList.add('ev2-dragging');
          head.style.cursor = 'grabbing';
        }
        if (moved) {
          var container = tile.parentElement;
          var maxLeft = container.offsetWidth  - tile.offsetWidth;
          var maxTop  = container.offsetHeight - 60; // allow partial overlap at bottom
          tile.style.left = Math.min(maxLeft, Math.max(0, sl + dx)) + 'px';
          tile.style.top  = Math.min(maxTop,  Math.max(0, st + dy)) + 'px';
        }
      }

      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        tile.classList.remove('ev2-dragging');
        head.style.cursor = '';
        if (moved) {
          wasDrag = true;
          savePositions();
        }
      }

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });

})();
