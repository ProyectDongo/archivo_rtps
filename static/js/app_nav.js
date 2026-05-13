/* App nav — auto-submit del selector de buzones (CSP-safe, sin onchange inline) */
(function () {
  'use strict';
  const select = document.getElementById('buzon-select');
  const form = document.getElementById('buzon-form');
  if (!select || !form) return;
  select.addEventListener('change', function () { form.submit(); });
})();
