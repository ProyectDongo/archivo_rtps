/* ==========================================================================
   Login del portal.
   - Toggle mostrar/ocultar password.
   - El captcha lo maneja Cloudflare Turnstile (auto, sin JS nuestro).
   ========================================================================== */
(function () {
  'use strict';

  const passToggle = document.getElementById('pass-toggle');
  const password   = document.getElementById('password');

  if (passToggle && password) {
    passToggle.addEventListener('click', function () {
      const showing = password.type === 'text';
      password.type = showing ? 'password' : 'text';
      passToggle.setAttribute(
        'aria-label',
        showing ? 'Mostrar contraseña' : 'Ocultar contraseña'
      );
    });
  }
})();
