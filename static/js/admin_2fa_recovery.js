(function () {
  var grid = document.getElementById('codes-grid');
  var btnC = document.getElementById('btn-copiar');
  var btnP = document.getElementById('btn-imprimir');
  function todos() {
    return Array.prototype.slice.call(grid.querySelectorAll('code'))
      .map(function (c) { return c.textContent.trim(); }).join('\n');
  }
  if (btnC) {
    btnC.addEventListener('click', function () {
      var t = todos();
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t).then(function () {
          btnC.textContent = '✓ Copiado';
          setTimeout(function () { btnC.textContent = 'Copiar todos'; }, 1800);
        });
      }
    });
  }
  if (btnP) {
    btnP.addEventListener('click', function () { window.print(); });
  }
})();
