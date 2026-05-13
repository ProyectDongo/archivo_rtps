// Compose flotante (Gmail-style): ventana en esquina inferior-derecha con
// autosave de borrador. Estados: hidden | normal | minimized | fullscreen.
// Persiste el draft en /intranet/borradores/ vía PM.post (CSRF auto).
(function () {
  'use strict';

  const fab = document.getElementById('compose-fab');
  if (!fab) return;
  const form = fab.querySelector('.compose-fab-form');
  const status = fab.querySelector('.compose-fab-status');
  const titleEl = fab.querySelector('.compose-fab-title');
  const inputs = {
    to:     form.querySelector('[name="to"]'),
    cc:     form.querySelector('[name="cc"]'),
    asunto: form.querySelector('[name="asunto"]'),
  };
  const cuerpoHidden = form.querySelector('.compose-fab-cuerpo-hidden');
  const ccRow = fab.querySelector('.compose-fab-row-cc');
  const ccToggle = fab.querySelector('.compose-fab-cc-toggle');
  const attachList = document.getElementById('cf-attach-list');
  const fileInput  = form.querySelector('.compose-fab-file-input');
  const attachBtn  = form.querySelector('.compose-fab-attach-btn');

  let estado = 'hidden';
  let borradorId = null;
  let modo = 'compose';
  let correoOriginalId = null;
  let saveTimer = null;
  let saveStatusTimer = null;
  let dirty = false;

  // ─── Quill (editor rico) ──────────────────────────────────────────────
  let quill = null;
  const editorEl = form.querySelector('.compose-fab-editor');
  if (editorEl && typeof Quill !== 'undefined') {
    quill = new Quill(editorEl, {
      theme: 'snow',
      modules: {
        toolbar: [['bold', 'italic', 'underline'], ['link'], ['clean']],
      },
      placeholder: editorEl.dataset.placeholder || 'Escribí tu mensaje…',
    });
    quill.on('text-change', function () {
      if (cuerpoHidden) cuerpoHidden.value = quill.root.innerHTML;
      dirty = true;
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(autosave, 1500);
    });
    quill.clipboard.addMatcher(Node.ELEMENT_NODE, function (node, delta) {
      return delta.compose(new window.Quill.imports['delta']([{ retain: delta.length(), attributes: { bold: null, italic: null, underline: null, color: null, background: null, size: null, font: null } }]));
    });
  }
  let attachments = [];

  function setEstado(nuevo) {
    estado = nuevo;
    fab.classList.toggle('is-minimized', estado === 'minimized');
    fab.classList.toggle('is-fullscreen', estado === 'fullscreen');
    fab.hidden = (estado === 'hidden');
    if (estado === 'hidden') document.body.style.overflow = '';
  }

  function setStatus(txt, cls) {
    if (!status) return;
    status.textContent = txt || '';
    status.className = 'compose-fab-status' + (cls ? ' ' + cls : '');
    if (saveStatusTimer) clearTimeout(saveStatusTimer);
    if (cls === 'saved') {
      saveStatusTimer = setTimeout(() => { status.textContent = ''; }, 4000);
    }
  }

  function payload() {
    const cuerpoVal = quill
      ? quill.root.innerHTML
      : (cuerpoHidden ? cuerpoHidden.value : '');
    return {
      to:     inputs.to.value || '',
      cc:     inputs.cc.value || '',
      asunto: inputs.asunto.value || '',
      cuerpo: cuerpoVal,
      modo:   modo,
    };
  }

  function vacio() {
    const cuerpoVacio = quill
      ? quill.getText().trim() === ''
      : !(cuerpoHidden && cuerpoHidden.value);
    return !inputs.to.value && !inputs.cc.value && !inputs.asunto.value && cuerpoVacio;
  }

  function autosave() {
    if (vacio()) return;
    setStatus('Guardando…', 'saving');
    const data = payload();
    if (!borradorId) {
      // Primera vez → POST crea
      if (correoOriginalId) data.correo_original_id = correoOriginalId;
      PM.post('/intranet/borradores/', data).then(function (resp) {
        if (resp && resp.id) {
          borradorId = resp.id;
          dirty = false;
          setStatus('Borrador guardado', 'saved');
        }
      }).catch(function () { setStatus('Error al guardar', 'error'); });
    } else {
      PM.post('/intranet/borradores/' + borradorId + '/', data).then(function () {
        dirty = false;
        setStatus('Borrador guardado', 'saved');
      }).catch(function () { setStatus('Error al guardar', 'error'); });
    }
  }

  function programarSave() {
    dirty = true;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(autosave, 1500);
  }

  // Re-arma el form con valores arbitrarios (al abrir o resumir un borrador).
  function poblar(data) {
    inputs.to.value     = (data && data.to)     || '';
    inputs.cc.value     = (data && data.cc)     || '';
    inputs.asunto.value = (data && data.asunto) || '';
    const cuerpoData = (data && data.cuerpo) || '';
    if (quill) {
      if (cuerpoData.trim().startsWith('<')) {
        quill.root.innerHTML = cuerpoData;
      } else {
        quill.setText(cuerpoData);
      }
      if (cuerpoHidden) cuerpoHidden.value = quill.root.innerHTML;
    } else if (cuerpoHidden) {
      cuerpoHidden.value = cuerpoData;
    }
    if (inputs.cc.value) {
      ccRow.hidden = false;
      ccToggle.hidden = true;
    } else {
      ccRow.hidden = true;
      ccToggle.hidden = false;
    }
    setStatus('');
  }

  // ─── API pública (window.PMCompose) ────────────────────────────────────
  window.PMCompose = {
    /**
     * Abre el compose flotante.
     * opts:
     *   borradorId          (opcional) → resume un draft existente
     *   modo                'compose' (default) | 'responder' | 'responder_todos' | 'reenviar'
     *   correoOriginalId    (para responder/reenviar)
     *   to, cc, asunto      strings prefill
     *   focus               'to' (default) | 'cuerpo'
     */
    open(opts) {
      opts = opts || {};
      modo = opts.modo || 'compose';
      correoOriginalId = opts.correoOriginalId || null;

      const titulos = {
        compose:         'Mensaje nuevo',
        responder:       'Responder',
        responder_todos: 'Responder a todos',
        reenviar:        'Reenviar',
      };
      titleEl.textContent = titulos[modo] || 'Mensaje nuevo';

      if (opts.borradorId) {
        borradorId = opts.borradorId;
        // Fetchear el borrador y poblar
        fetch('/intranet/borradores/' + borradorId + '/', {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'fetch' },
        })
          .then(r => r.json())
          .then(data => {
            modo = data.modo || modo;
            correoOriginalId = data.correo_original_id || null;
            poblar(data);
          })
          .catch(() => { setStatus('No se pudo cargar el borrador', 'error'); });
      } else {
        borradorId = null;
        attachments = [];
        renderAttachments();
        poblar({
          to:     opts.to || '',
          cc:     opts.cc || '',
          asunto: opts.asunto || '',
          cuerpo: opts.cuerpo || '',
        });
      }

      setEstado('normal');
      const focusEl = (opts.focus === 'cuerpo') ? inputs.cuerpo : inputs.to;
      setTimeout(() => focusEl.focus(), 50);
    },
    minimize() { setEstado('minimized'); },
    expand()   { setEstado('fullscreen'); },
    restore()  { setEstado('normal'); },
    close()    {
      if (dirty) autosave();   // last save antes de cerrar
      setEstado('hidden');
    },
  };

  // ─── Inputs y autosave ────────────────────────────────────────────────
  ['to', 'cc', 'asunto'].forEach(k => {
    inputs[k].addEventListener('input', programarSave);
  });

  // ─── Header buttons ───────────────────────────────────────────────────
  fab.querySelectorAll('[data-cf-action]').forEach(btn => {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      const action = btn.getAttribute('data-cf-action');
      if (action === 'minimize') {
        if (estado === 'minimized') setEstado('normal');
        else setEstado('minimized');
      } else if (action === 'expand') {
        setEstado(estado === 'fullscreen' ? 'normal' : 'fullscreen');
      } else if (action === 'close') {
        window.PMCompose.close();
      } else if (action === 'toggle-cc') {
        ccRow.hidden = false;
        ccToggle.hidden = true;
        inputs.cc.focus();
      } else if (action === 'discard') {
        if (!confirm('¿Descartar este borrador? Se borra para siempre.')) return;
        if (borradorId) {
          fetch('/intranet/borradores/' + borradorId + '/', {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: {
              'X-Requested-With': 'fetch',
              'X-CSRFToken': PM.csrf,
            },
          });
        }
        borradorId = null;
        attachments = [];
        renderAttachments();
        setEstado('hidden');
      }
    });
  });

  // Click en barra header (no botones) cuando minimizado → restaurar
  const head = fab.querySelector('.compose-fab-head');
  head.addEventListener('click', function (e) {
    if (e.target.closest('[data-cf-action]')) return;
    if (estado === 'minimized') setEstado('normal');
  });

  // ─── Adjuntos ─────────────────────────────────────────────────────────

  function formatSize(b) {
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }

  function renderAttachments() {
    if (!attachList) return;
    attachList.innerHTML = '';
    attachments.forEach(function (a) {
      const item = document.createElement('div');
      item.className = 'cf-attach-item';
      item.innerHTML =
        '<span class="cf-attach-name" title="' + a.nombre + '">' + a.nombre + '</span>'
        + '<span class="cf-attach-size">' + formatSize(a.tamanio) + '</span>'
        + '<button type="button" class="cf-attach-remove" data-id="' + a.id + '" aria-label="Quitar adjunto">×</button>';
      attachList.appendChild(item);
    });
    attachList.hidden = attachments.length === 0;
  }

  function removeAttachment(adjId) {
    if (!borradorId) return;
    fetch('/intranet/borradores/' + borradorId + '/adjuntos/' + adjId + '/', {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': PM.csrf, 'X-Requested-With': 'fetch' },
    }).then(function () {
      attachments = attachments.filter(function (a) { return a.id !== adjId; });
      renderAttachments();
    });
  }

  function uploadFiles(files) {
    if (!files || files.length === 0) return;
    function doUpload() {
      Array.from(files).forEach(function (file) {
        const fd = new FormData();
        fd.append('file', file);
        setStatus('Subiendo adjunto…', 'saving');
        fetch('/intranet/borradores/' + borradorId + '/adjuntos/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': PM.csrf, 'X-Requested-With': 'fetch' },
          body: fd,
        })
          .then(function (r) { return r.json(); })
          .then(function (resp) {
            if (resp.id) {
              attachments.push({ id: resp.id, nombre: resp.nombre, tamanio: resp.tamanio });
              renderAttachments();
              setStatus('Adjunto subido', 'saved');
            } else {
              setStatus(resp.error || 'Error al subir adjunto', 'error');
            }
          })
          .catch(function () { setStatus('Error al subir adjunto', 'error'); });
      });
    }
    if (!borradorId) {
      const data = payload();
      if (correoOriginalId) data.correo_original_id = correoOriginalId;
      PM.post('/intranet/borradores/', data).then(function (resp) {
        if (resp && resp.id) { borradorId = resp.id; doUpload(); }
      });
    } else {
      doUpload();
    }
  }

  if (attachBtn) {
    attachBtn.addEventListener('click', function () { if (fileInput) fileInput.click(); });
  }
  if (fileInput) {
    fileInput.addEventListener('change', function () {
      uploadFiles(fileInput.files);
      fileInput.value = '';
    });
  }
  if (attachList) {
    attachList.addEventListener('click', function (e) {
      const btn = e.target.closest('.cf-attach-remove');
      if (btn) removeAttachment(parseInt(btn.dataset.id, 10));
    });
  }

  fab.addEventListener('dragover', function (e) {
    e.preventDefault();
    fab.classList.add('is-dropping');
  });
  fab.addEventListener('dragleave', function (e) {
    if (!fab.contains(e.relatedTarget)) fab.classList.remove('is-dropping');
  });
  fab.addEventListener('drop', function (e) {
    e.preventDefault();
    fab.classList.remove('is-dropping');
    uploadFiles(e.dataTransfer.files);
  });

  // ─── Submit (Enviar) ──────────────────────────────────────────────────
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    // Asegurar que existe el borrador (si el usuario tipeó muy rápido y mandó antes del autosave)
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }

    const enviarFinal = function () {
      setStatus('Enviando…', 'saving');
      const body = new URLSearchParams();
      const data = payload();
      Object.keys(data).forEach(k => body.append(k, data[k]));
      fetch('/intranet/borradores/' + borradorId + '/enviar/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': PM.csrf,
          'X-Requested-With': 'fetch',
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: body.toString(),
      })
        .then(r => r.json().then(j => ({ status: r.status, body: j })))
        .then(function (resp) {
          if (resp.body && resp.body.ok) {
            setStatus('');
            setEstado('hidden');
            borradorId = null;
            if (window.location.pathname.includes('/intranet/bandeja')) {
              window.location.reload();
            } else {
              alert('Correo enviado a ' + (resp.body.enviado_a || []).join(', '));
            }
          } else {
            setStatus((resp.body && resp.body.error) || 'Error al enviar', 'error');
          }
        })
        .catch(function () { setStatus('Error de red al enviar', 'error'); });
    };

    if (!borradorId) {
      // Crear primero, luego enviar
      const data = payload();
      if (correoOriginalId) data.correo_original_id = correoOriginalId;
      PM.post('/intranet/borradores/', data).then(function (resp) {
        if (resp && resp.id) {
          borradorId = resp.id;
          enviarFinal();
        } else {
          setStatus('No se pudo crear el borrador', 'error');
        }
      }).catch(function () { setStatus('Error al crear borrador', 'error'); });
    } else {
      enviarFinal();
    }
  });

  // Cerrar con Esc cuando está en fullscreen (en normal/minimized no, para no perder texto sin querer)
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && estado === 'fullscreen') {
      setEstado('normal');
    }
  });

  // ─── Hijack del link "Redactar" del sidebar ─────────────────────────────
  document.addEventListener('click', function (e) {
    const link = e.target.closest('.sidebar-compose');
    if (!link) return;
    e.preventDefault();
    window.PMCompose.open({ modo: 'compose' });
  });

  // ─── Sidebar "Borradores" popover + resumir draft ──────────────────────
  const draftsBtn = document.getElementById('sidebar-drafts-btn');
  const draftsPop = document.getElementById('sidebar-drafts-pop');
  if (draftsBtn && draftsPop) {
    draftsBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      const abrir = draftsPop.hidden;
      draftsPop.hidden = !abrir;
      draftsBtn.setAttribute('aria-expanded', String(abrir));
    });
    document.addEventListener('click', function (e) {
      if (!draftsPop.hidden && !draftsPop.contains(e.target) && e.target !== draftsBtn) {
        draftsPop.hidden = true;
        draftsBtn.setAttribute('aria-expanded', 'false');
      }
    });
    draftsPop.querySelectorAll('.sidebar-draft-item').forEach(function (it) {
      it.addEventListener('click', function () {
        const bid = it.getAttribute('data-borrador-id');
        if (!bid) return;
        draftsPop.hidden = true;
        draftsBtn.setAttribute('aria-expanded', 'false');
        window.PMCompose.open({ borradorId: parseInt(bid, 10) });
      });
    });
  }

  // ─── Hijack de "Responder" / "Resp. todos" / "Reenviar" del preview ────
  // Abren el compose flotante con prefill, en vez de navegar a /responder.
  document.addEventListener('click', function (e) {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
    const link = e.target.closest('.preview-reply-btn, .preview-fwd-btn');
    if (!link) return;
    const card = link.closest('.preview-card');
    if (!card) return;
    const correoId = card.getAttribute('data-correo-id');
    if (!correoId) return;

    let modo = 'simple';
    if (link.classList.contains('preview-fwd-btn')) {
      modo = 'reenviar';
    } else {
      const href = link.getAttribute('href') || '';
      if (href.includes('modo=todos')) modo = 'todos';
    }
    e.preventDefault();

    fetch('/intranet/correo/' + correoId + '/prefill/?modo=' + modo, {
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch' },
    })
      .then(r => r.json())
      .then(function (pref) {
        window.PMCompose.open({
          modo:             pref.modo,
          correoOriginalId: parseInt(correoId, 10),
          to:               pref.to || '',
          cc:               pref.cc || '',
          asunto:           pref.asunto || '',
          focus:            'cuerpo',
        });
      })
      .catch(function () {
        // Fallback: si el prefill falla, navegar al flow viejo.
        window.location.href = link.getAttribute('href');
      });
  });
})();
