// Modal inline para preview de adjuntos no-imagen (PDF, audio, video).
// Las imágenes las maneja lightbox.js.
(function () {
  'use strict';

  const vw = document.getElementById('adj-viewer');
  if (!vw) return;
  const body    = vw.querySelector('.adj-viewer-body');
  const nameEl  = vw.querySelector('.adj-viewer-name');
  const sizeEl  = vw.querySelector('.adj-viewer-size');
  const dlEl    = vw.querySelector('.adj-viewer-dl');
  const tabEl   = vw.querySelector('.adj-viewer-tab');
  const closeBtn = vw.querySelector('.adj-viewer-close');

  function previewable(mime) {
    mime = (mime || '').toLowerCase();
    if (mime === 'application/pdf') return 'pdf';
    if (mime.startsWith('audio/')) return 'audio';
    if (mime.startsWith('video/')) return 'video';
    return null;
  }

  function abrir(href, mime, name, size) {
    const tipo = previewable(mime);
    if (!tipo) return false;

    nameEl.textContent = name || '';
    sizeEl.textContent = size ? '· ' + size : '';
    dlEl.href = href;
    dlEl.setAttribute('download', name || '');
    tabEl.href = href;

    body.innerHTML = '';

    if (tipo === 'pdf') {
      const loader = document.createElement('div');
      loader.className = 'adj-viewer-loading';
      loader.innerHTML = '<div class="adj-viewer-spinner"></div><span>Cargando…</span>';
      body.appendChild(loader);

      // Fetch como blob: envía cookie de sesión y crea un blob URL
      // que no está sujeto a restricciones frame-src/object-src del CSP.
      fetch(href, { credentials: 'same-origin' })
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.blob();
        })
        .then(function (blob) {
          var blobUrl = URL.createObjectURL(blob);
          var emb = document.createElement('embed');
          emb.className = 'adj-viewer-iframe';
          emb.type = 'application/pdf';
          emb.src = blobUrl;
          emb._blobUrl = blobUrl;
          loader.hidden = true;
          body.appendChild(emb);
        })
        .catch(function () {
          loader.hidden = true;
          body.innerHTML =
            '<div style="padding:32px;text-align:center;color:#666">' +
            '<p>No se pudo previsualizar el PDF.</p>' +
            '<a href="' + href + '" target="_blank" rel="noopener" ' +
            'style="color:#1a73e8">Abrir en nueva pestaña ↗</a></div>';
        });
    } else if (tipo === 'audio') {
      const a = document.createElement('audio');
      a.controls = true;
      a.src = href;
      a.style.width = '100%';
      body.appendChild(a);
    } else if (tipo === 'video') {
      const v = document.createElement('video');
      v.controls = true;
      v.src = href;
      v.style.maxWidth = '100%';
      v.style.maxHeight = '80vh';
      body.appendChild(v);
    }

    vw.hidden = false;
    document.body.style.overflow = 'hidden';
    return true;
  }

  function cerrar() {
    vw.hidden = true;
    // Liberar blob URLs para no acumular memoria
    body.querySelectorAll('[_blobUrl]').forEach(function (el) {
      try { URL.revokeObjectURL(el._blobUrl); } catch (e) {}
    });
    body.innerHTML = '';
    document.body.style.overflow = '';
  }

  document.addEventListener('click', function (e) {
    const card = e.target.closest('.adj-card');
    if (!card) return;
    if (e.target.closest('[download]')) return;  // botón descargar: no interceptar
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
    const mime = card.getAttribute('data-mime') || '';
    if (!previewable(mime)) return;
    e.preventDefault();
    abrir(
      card.getAttribute('href'),
      mime,
      card.getAttribute('data-name') || '',
      card.getAttribute('data-size') || ''
    );
  });

  closeBtn.addEventListener('click', cerrar);
  vw.addEventListener('click', function (e) { if (e.target === vw) cerrar(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !vw.hidden) cerrar();
  });
})();
