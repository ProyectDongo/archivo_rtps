// Acciones de la página de recovery codes (sin JS inline para no relajar CSP).
(function () {
  'use strict';

  const grid = document.getElementById('codes-grid');
  const btnCopiar = document.getElementById('btn-copiar-codes');
  const btnImprimir = document.getElementById('btn-imprimir-codes');

  function todosLosCodes() {
    if (!grid) return '';
    return Array.from(grid.querySelectorAll('code'))
      .map(function (c) { return c.textContent.trim(); })
      .join('\n');
  }

  if (btnCopiar) {
    btnCopiar.addEventListener('click', function () {
      const texto = todosLosCodes();
      if (!texto) return;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(texto).then(
          function () {
            btnCopiar.textContent = '✓ Copiado';
            setTimeout(function () { btnCopiar.textContent = 'Copiar todos'; }, 1800);
          },
          function () { fallbackCopiar(texto); },
        );
      } else {
        fallbackCopiar(texto);
      }
    });
  }

  function fallbackCopiar(texto) {
    const ta = document.createElement('textarea');
    ta.value = texto;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* nada */ }
    document.body.removeChild(ta);
  }

  if (btnImprimir) {
    btnImprimir.addEventListener('click', function () {
      window.print();
    });
  }
})();
