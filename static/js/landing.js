/* ==========================================================================
   Landing — interacciones del lado cliente
   - Scroll reveal con IntersectionObserver
   - Menú hamburguesa móvil
   - Smooth scroll a secciones
   - URL del portal interno NO está aquí. Solo es un <a href> en el HTML.
   ========================================================================== */

(function () {
  'use strict';

  // ─── Scroll reveal ─────────────────────────────────────────
  if ('IntersectionObserver' in window) {
    const obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add('on');
          obs.unobserve(e.target);
        }
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });

    document.querySelectorAll('.rv, .rvl, .rvr, .fx-zoom, .fx-blur, .fx-tilt').forEach(function (el) {
      obs.observe(el);
    });
  } else {
    // Sin IntersectionObserver: muestra todo de una
    document.querySelectorAll('.rv, .rvl, .rvr, .fx-zoom, .fx-blur, .fx-tilt').forEach(function (el) {
      el.classList.add('on');
    });
  }

  // ─── Menú móvil ────────────────────────────────────────────
  const ham = document.getElementById('ham-btn');
  const mobMenu = document.getElementById('mob-menu');
  if (ham && mobMenu) {
    ham.addEventListener('click', function () {
      mobMenu.classList.toggle('open');
    });
    mobMenu.querySelectorAll('.nl-m').forEach(function (a) {
      a.addEventListener('click', function () { mobMenu.classList.remove('open'); });
    });
  }

  // ─── Botones internos con scroll suave ─────────────────────
  document.querySelectorAll('[data-scroll-to]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const target = document.getElementById(btn.dataset.scrollTo);
      if (target) target.scrollIntoView({ behavior: 'smooth' });
    });
  });
})();
