// Lightbox liviano para previsualizar imágenes adjuntas sin recargar.
// Sin dependencias. CSP-safe (todo via addEventListener).
// Soporta: navegación entre imágenes, contador, descarga, teclado, swipe.
(function () {
  'use strict';

  const lb = document.getElementById('lightbox');
  if (!lb) return;
  const img = lb.querySelector('.lightbox-img');
  const capText = lb.querySelector('.lightbox-cap-text');
  const counter = lb.querySelector('.lightbox-counter');
  const dlBtn = lb.querySelector('.lightbox-download');
  const closeBtn = lb.querySelector('.lightbox-close');
  const prevBtn = lb.querySelector('.lightbox-prev');
  const nextBtn = lb.querySelector('.lightbox-next');

  let galeria = [];     // [{src, name, size}]
  let idx = 0;

  // Recolecta todos los thumbnails visibles del documento (preview pane o detalle).
  function recolectarGaleria() {
    return Array.from(document.querySelectorAll('.adj-thumb')).map(t => ({
      src: t.getAttribute('href'),
      name: t.getAttribute('data-name') || '',
      size: t.getAttribute('data-size') || '',
    }));
  }

  function pintar() {
    if (!galeria.length) return;
    const it = galeria[idx];
    img.src = it.src;
    img.alt = it.name || '';
    capText.textContent = it.name + (it.size ? ' · ' + it.size : '');
    if (galeria.length > 1) {
      counter.textContent = (idx + 1) + ' / ' + galeria.length;
      counter.hidden = false;
      prevBtn.hidden = false;
      nextBtn.hidden = false;
    } else {
      counter.hidden = true;
      prevBtn.hidden = true;
      nextBtn.hidden = true;
    }
    if (dlBtn) {
      dlBtn.href = it.src;
      dlBtn.setAttribute('download', it.name || '');
    }
  }

  function abrir(startIdx) {
    galeria = recolectarGaleria();
    if (!galeria.length) return;
    idx = Math.max(0, Math.min(startIdx | 0, galeria.length - 1));
    pintar();
    lb.hidden = false;
    document.body.style.overflow = 'hidden';
  }

  function cerrar() {
    lb.hidden = true;
    img.src = '';
    document.body.style.overflow = '';
  }

  function ir(delta) {
    if (galeria.length < 2) return;
    idx = (idx + delta + galeria.length) % galeria.length;
    pintar();
  }

  // Click en thumbnail → abrir en su posición.
  document.addEventListener('click', function (e) {
    const t = e.target.closest('.adj-thumb');
    if (!t) return;
    e.preventDefault();
    const all = recolectarGaleria();
    const i = Array.from(document.querySelectorAll('.adj-thumb')).indexOf(t);
    galeria = all;
    idx = i >= 0 ? i : 0;
    pintar();
    lb.hidden = false;
    document.body.style.overflow = 'hidden';
  });

  // Cerrar: click en backdrop, en la X.
  lb.addEventListener('click', function (e) {
    if (e.target === lb || e.target === lb.querySelector('.lightbox-figure')) cerrar();
  });
  closeBtn.addEventListener('click', cerrar);
  prevBtn.addEventListener('click', function (e) { e.stopPropagation(); ir(-1); });
  nextBtn.addEventListener('click', function (e) { e.stopPropagation(); ir(1); });

  // Teclado.
  document.addEventListener('keydown', function (e) {
    if (lb.hidden) return;
    if (e.key === 'Escape') { cerrar(); return; }
    if (e.key === 'ArrowLeft') { ir(-1); return; }
    if (e.key === 'ArrowRight') { ir(1); return; }
    if (e.key === 'd' || e.key === 'D') {
      // Forzar descarga sin que el atajo del navegador (Ctrl+S) capture el foco.
      if (dlBtn && dlBtn.href) dlBtn.click();
    }
  });

  // Swipe horizontal en mobile.
  let touchX = null;
  lb.addEventListener('touchstart', function (e) {
    if (e.touches.length === 1) touchX = e.touches[0].clientX;
  }, { passive: true });
  lb.addEventListener('touchend', function (e) {
    if (touchX == null) return;
    const dx = (e.changedTouches[0].clientX || 0) - touchX;
    if (Math.abs(dx) > 50) ir(dx > 0 ? -1 : 1);
    touchX = null;
  }, { passive: true });
})();
