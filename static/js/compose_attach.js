// Compose: dropzone + lista visual de adjuntos. El input <input type=file multiple>
// es la fuente de verdad — usamos un DataTransfer para poder agregar/quitar
// archivos antes del submit.
(function () {
  'use strict';

  const dz = document.getElementById('compose-dropzone');
  const input = document.getElementById('compose-files');
  const list = document.getElementById('compose-attach-list');
  const form = document.getElementById('compose-form');
  if (!dz || !input || !list || !form) return;

  const MAX_FILES = 10;
  const MAX_TOTAL_MB = 25;
  const MAX_TOTAL = MAX_TOTAL_MB * 1024 * 1024;
  const BLOCKED_EXT = /\.(exe|bat|cmd|com|scr|msi|vbs|js|jar|ps1|sh|app|dmg)$/i;

  // El DataTransfer es mutable; FileList no.
  const dt = new DataTransfer();

  function fmtSize(n) {
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }

  function totalActual() {
    let t = 0;
    for (const f of dt.files) t += f.size;
    return t;
  }

  function pintar() {
    input.files = dt.files;
    list.innerHTML = '';
    if (dt.files.length === 0) {
      list.hidden = true;
      return;
    }
    list.hidden = false;
    Array.from(dt.files).forEach(function (f, idx) {
      const li = document.createElement('li');
      li.className = 'compose-attach-item';
      const name = document.createElement('span');
      name.className = 'compose-attach-name';
      name.textContent = f.name;
      name.title = f.name;
      const size = document.createElement('span');
      size.className = 'compose-attach-size';
      size.textContent = fmtSize(f.size);
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'compose-attach-rm';
      rm.setAttribute('aria-label', 'Quitar adjunto');
      rm.title = 'Quitar';
      rm.textContent = '✕';
      rm.addEventListener('click', function () {
        const ndt = new DataTransfer();
        Array.from(dt.files).forEach((ff, i) => { if (i !== idx) ndt.items.add(ff); });
        dt.items.clear();
        Array.from(ndt.files).forEach(ff => dt.items.add(ff));
        pintar();
      });
      li.appendChild(name);
      li.appendChild(size);
      li.appendChild(rm);
      list.appendChild(li);
    });

    // Total al final
    const tot = document.createElement('li');
    tot.className = 'compose-attach-total';
    tot.textContent = dt.files.length + ' archivo' + (dt.files.length === 1 ? '' : 's') +
      ' · ' + fmtSize(totalActual()) + ' / ' + MAX_TOTAL_MB + ' MB';
    list.appendChild(tot);
  }

  function agregar(files) {
    const existentes = new Set(Array.from(dt.files).map(f => f.name + '|' + f.size));
    let mensajeError = '';
    for (const f of files) {
      if (dt.files.length >= MAX_FILES) {
        mensajeError = 'Máximo ' + MAX_FILES + ' archivos por correo.';
        break;
      }
      if (BLOCKED_EXT.test(f.name)) {
        mensajeError = 'Tipo de archivo no permitido: ' + f.name;
        continue;
      }
      const key = f.name + '|' + f.size;
      if (existentes.has(key)) continue;   // dedup por nombre+tamaño
      // Probar si entra dentro del total
      if (totalActual() + f.size > MAX_TOTAL) {
        mensajeError = 'Superás el límite de ' + MAX_TOTAL_MB + ' MB total.';
        break;
      }
      dt.items.add(f);
      existentes.add(key);
    }
    if (mensajeError) alert(mensajeError);
    pintar();
  }

  // Click en dropzone abre el selector
  dz.addEventListener('click', function (e) {
    if (e.target.closest('.compose-attach-rm')) return;
    input.click();
  });
  dz.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      input.click();
    }
  });

  // Cuando cambia el input (selección desde diálogo), agregamos a dt y limpiamos input.
  input.addEventListener('change', function () {
    if (input.files && input.files.length) {
      agregar(input.files);
    }
  });

  // Drag & drop
  ['dragenter', 'dragover'].forEach(ev => {
    dz.addEventListener(ev, function (e) {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.add('is-dragover');
    });
  });
  ['dragleave', 'drop'].forEach(ev => {
    dz.addEventListener(ev, function (e) {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.remove('is-dragover');
    });
  });
  dz.addEventListener('drop', function (e) {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      agregar(e.dataTransfer.files);
    }
  });
})();
