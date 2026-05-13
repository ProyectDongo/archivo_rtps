// Anti-FOUC: aplica tema y densidad antes de pintar.
// Cargado SYNC (sin defer) desde <head> para que corra antes del primer
// paint y evite un flash. Externalizado por CSP estricta (sin unsafe-inline).
(function () {
  try {
    var t = localStorage.getItem('pm.theme');
    var d = localStorage.getItem('pm.density');
    if (t === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    if (d === 'compact') document.documentElement.setAttribute('data-density', 'compact');
  } catch (e) {}
})();
