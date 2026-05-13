/* ============================================================
   JS de Archivos / Contratos / Papelera.
   Vanilla, sin libs. CSP-compliant (cero inline scripts).
   Incluye: upload modal + drag&drop + preview inline + delete confirm.
   ============================================================ */
(function () {
  'use strict';

  /* ────────────────────────────────────────────────────────────────
     Preview inline — modal con iframe/img según mime
     ──────────────────────────────────────────────────────────────── */
  var previewOverlay = null;
  function buildPreviewOverlay() {
    if (previewOverlay) return previewOverlay;
    previewOverlay = document.createElement('div');
    previewOverlay.className = 'arc-preview-overlay';
    previewOverlay.innerHTML =
      '<div class="arc-preview-bar">' +
        '<span class="arc-preview-title"></span>' +
        '<span class="arc-preview-meta"></span>' +
        '<a class="arc-preview-dl" href="#" download title="Descargar">⬇ Descargar</a>' +
        '<button type="button" class="arc-preview-close" aria-label="Cerrar (Esc)">✕</button>' +
      '</div>' +
      '<div class="arc-preview-body"></div>';
    document.body.appendChild(previewOverlay);
    previewOverlay.querySelector('.arc-preview-close')
      .addEventListener('click', closePreview);
    previewOverlay.addEventListener('click', function (e) {
      if (e.target === previewOverlay) closePreview();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && previewOverlay.classList.contains('open')) {
        closePreview();
      }
    });
    return previewOverlay;
  }
  function openPreview(url, name, mime, size) {
    var ov = buildPreviewOverlay();
    var body = ov.querySelector('.arc-preview-body');
    var dl = ov.querySelector('.arc-preview-dl');
    body.innerHTML = '';
    ov.querySelector('.arc-preview-title').textContent = name || '';
    ov.querySelector('.arc-preview-meta').textContent = size || '';
    // Para la descarga sacamos ?inline=1
    dl.href = url.replace(/[?&]inline=1/, '').replace(/\?$/, '');
    dl.setAttribute('download', name || '');

    var m = (mime || '').toLowerCase();
    var node;
    if (m.indexOf('image/') === 0) {
      node = document.createElement('img');
      node.src = url; node.alt = name || ''; node.className = 'arc-preview-img';
    } else if (m === 'application/pdf') {
      node = document.createElement('iframe');
      node.src = url; node.className = 'arc-preview-iframe';
      node.setAttribute('title', name || 'PDF');
    } else if (m.indexOf('audio/') === 0) {
      node = document.createElement('audio');
      node.controls = true; node.src = url; node.className = 'arc-preview-audio';
    } else if (m.indexOf('video/') === 0) {
      node = document.createElement('video');
      node.controls = true; node.src = url; node.className = 'arc-preview-video';
    } else if (m.indexOf('text/') === 0) {
      node = document.createElement('iframe');
      node.src = url; node.className = 'arc-preview-iframe';
      node.setAttribute('title', name || 'Texto');
    } else {
      node = document.createElement('div');
      node.className = 'arc-preview-fallback';
      node.innerHTML = '<p>No hay vista previa disponible para este tipo de archivo.</p>' +
                      '<a class="arc-btn-primary" href="' + dl.href + '" download>' +
                      '⬇ Descargar ' + (name || '') + '</a>';
    }
    body.appendChild(node);
    ov.classList.add('open');
    document.body.style.overflow = 'hidden';
  }
  function closePreview() {
    if (!previewOverlay) return;
    previewOverlay.classList.remove('open');
    previewOverlay.querySelector('.arc-preview-body').innerHTML = '';
    document.body.style.overflow = '';
  }
  document.querySelectorAll('.arc-btn-preview').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openPreview(
        btn.getAttribute('data-preview-url'),
        btn.getAttribute('data-preview-name'),
        btn.getAttribute('data-preview-mime'),
        btn.getAttribute('data-preview-size')
      );
    });
  });

  /* ────────────────────────────────────────────────────────────────
     Modal de upload (existing)
     ──────────────────────────────────────────────────────────────── */
  var modal = document.getElementById('upload-modal');
  if (!modal) return;

  function open() {
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
  }
  function close() {
    modal.hidden = true;
    document.body.style.overflow = '';
  }

  // Botones que abren el modal (puede haber más de uno: header + empty state)
  var openers = document.querySelectorAll(
    '#btn-subir-archivo, #btn-subir-archivo-empty'
  );
  openers.forEach(function (b) {
    b.addEventListener('click', open);
  });

  // Botones que cierran el modal (backdrop, X, cancelar)
  modal.querySelectorAll('[data-close-modal]').forEach(function (b) {
    b.addEventListener('click', close);
  });

  // Esc cierra
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !modal.hidden) close();
  });

  // Mostrar campos contrato solo cuando tipo=contrato (en form de Archivos
  // donde el select de tipo existe). En la vista Contratos siempre van.
  var tipoSelect = modal.querySelector('select[name="tipo"]');
  var contratoFields = modal.querySelectorAll('[data-contrato-only]');
  if (tipoSelect && contratoFields.length) {
    function syncContratoFields() {
      var show = tipoSelect.value === 'contrato';
      contratoFields.forEach(function (el) {
        el.hidden = !show;
      });
    }
    tipoSelect.addEventListener('change', syncContratoFields);
    syncContratoFields();
  }

  /* ────────────────────────────────────────────────────────────────
     Drag & drop sobre la zona de upload
     ──────────────────────────────────────────────────────────────── */
  var dropzone = modal.querySelector('.arc-dropzone');
  var fileInput = modal.querySelector('input[type="file"][name="archivo"]');
  var fileLabel = modal.querySelector('.arc-dropzone-label');
  if (dropzone && fileInput) {

    function describeFile(file) {
      if (!file) return '';
      var kb = file.size / 1024;
      var sizeStr = kb < 1024 ? kb.toFixed(0) + ' KB'
                              : (kb / 1024).toFixed(1) + ' MB';
      return file.name + ' · ' + sizeStr;
    }

    function updateLabel() {
      if (!fileLabel) return;
      if (fileInput.files && fileInput.files.length > 0) {
        fileLabel.textContent = describeFile(fileInput.files[0]);
        dropzone.classList.add('has-file');
      } else {
        fileLabel.textContent = 'Arrastrá un archivo o hacé click para elegir';
        dropzone.classList.remove('has-file');
      }
    }

    fileInput.addEventListener('change', updateLabel);
    updateLabel();

    // Click en cualquier lado de la dropzone activa el input
    dropzone.addEventListener('click', function (e) {
      if (e.target === fileInput) return;
      fileInput.click();
    });

    ['dragenter', 'dragover'].forEach(function (evt) {
      dropzone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.add('dragging');
      });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      dropzone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.remove('dragging');
      });
    });

    dropzone.addEventListener('drop', function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      var dt = new DataTransfer();
      // Solo 1 archivo (el input no es multiple); tomamos el primero del drop
      dt.items.add(e.dataTransfer.files[0]);
      fileInput.files = dt.files;
      updateLabel();
      // Auto-fill del nombre con el filename si el user no escribió uno propio
      var nombreInput = modal.querySelector('input[name="nombre"]');
      if (nombreInput && !nombreInput.value.trim()) {
        nombreInput.value = e.dataTransfer.files[0].name;
      }
    });
  }
})();
