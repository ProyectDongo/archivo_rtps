/* Cookie consent — solo páginas públicas. La decisión se persiste en
   localStorage como `pm:cookies:consent` con valores:
     - "all"        → cookies esenciales + opcionales (analytics, etc.)
     - "essential"  → solo lo estrictamente necesario para que el sitio
                      funcione (sesión, CSRF, preferencias de UI)
     - undefined    → todavía no decidió → mostrar banner

   Si el día de mañana metemos un beacon de Cloudflare Web Analytics o
   similar, este archivo es el único lugar donde se decide cargarlo o no.
   La función `loadAnalytics()` queda preparada como hook. */

(function () {
  'use strict';

  const STORAGE_KEY = 'pm:cookies:consent';
  const banner = document.getElementById('cookie-banner');
  if (!banner) return;

  function getConsent() {
    try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
  }
  function setConsent(value) {
    try { localStorage.setItem(STORAGE_KEY, value); } catch (e) { /* private mode */ }
  }

  function loadAnalytics() {
    /* Hook: cargar trackers opcionales SOLO si el usuario aceptó.
       Hoy no inyectamos beacons de terceros — Cloudflare RUM viene
       desde el lado del proxy y no podemos bloquearlo desde acá.
       Si en el futuro metemos GA / Plausible / etc., se hace acá. */
  }

  // Decisión previa: aplicar y NO mostrar banner.
  const prev = getConsent();
  if (prev === 'all') {
    loadAnalytics();
    return;
  }
  if (prev === 'essential') {
    return;
  }

  // Sin decisión previa → mostrar banner.
  banner.hidden = false;

  const btnAll = document.getElementById('cookie-accept-all');
  const btnEssentials = document.getElementById('cookie-essentials');

  function ocultarConFade() {
    banner.style.transition = 'opacity .25s ease, transform .3s ease';
    banner.style.opacity = '0';
    banner.style.transform = 'translateY(20px)';
    setTimeout(function () { banner.hidden = true; }, 320);
  }

  if (btnAll) {
    btnAll.addEventListener('click', function () {
      setConsent('all');
      loadAnalytics();
      ocultarConFade();
    });
  }
  if (btnEssentials) {
    btnEssentials.addEventListener('click', function () {
      setConsent('essential');
      ocultarConFade();
    });
  }
})();
