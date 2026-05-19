(function () {
  'use strict';

  // ── Clock ─────────────────────────────────────────────────────────
  function updateClock() {
    var el = document.getElementById('ev2-clock');
    if (!el) return;
    var now = new Date();
    el.textContent =
      String(now.getHours()).padStart(2, '0') + ':' +
      String(now.getMinutes()).padStart(2, '0');
  }
  updateClock();
  setInterval(updateClock, 10000);

  // ── Time-of-day greeting ──────────────────────────────────────────
  var greetingEl = document.getElementById('ev2-greeting-text');
  if (greetingEl) {
    var strong = greetingEl.querySelector('strong');
    var hour   = new Date().getHours();
    var prefix = hour < 12 ? 'Buenos días, '
               : hour < 19 ? 'Buenas tardes, '
               :              'Buenas noches, ';
    greetingEl.textContent = '';
    greetingEl.appendChild(document.createTextNode(prefix));
    if (strong) greetingEl.appendChild(strong);
  }

  // ── Tile expand / collapse ────────────────────────────────────────
  document.querySelectorAll('.ev2-tile').forEach(function (tile) {
    var head = tile.querySelector('.ev2-tile-head');
    if (!head) return;
    head.addEventListener('click', function () {
      tile.classList.toggle('open');
    });
  });

  // ── Colapsar button — cierra todos los tiles abiertos ─────────────
  var btnColapsar = document.getElementById('ev2-btn-colapsar');
  if (btnColapsar) {
    btnColapsar.addEventListener('click', function () {
      document.querySelectorAll('.ev2-tile.open').forEach(function (t) {
        t.classList.remove('open');
      });
    });
  }
})();
