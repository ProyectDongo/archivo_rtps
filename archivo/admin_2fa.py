"""
Admin 2FA — middleware + vistas + URLs.

Flujo:
  1. Staff entra a /admin-*/login/ → autentica con user/pass Django normal.
  2. Admin2FAMiddleware detecta que el usuario es staff pero admin_2fa_ok
     no está en sesión → redirige a /admin-*/2fa/verify/.
  3. /admin-*/2fa/verify/: TOTP o recovery code. Si ok → session['admin_2fa_ok']=True.
  4. /admin-*/2fa/setup/: vista para configurar TOTP (genera QR).
  5. /admin-*/2fa/recovery/: muestra códigos de recuperación.

Si el admin no tiene AdminTOTP configurado, puede entrar al admin SIN 2FA
(para el primer setup). Una vez activado, es obligatorio.
"""
from __future__ import annotations

import io
import logging

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import path
from django.utils import timezone

logger = logging.getLogger('correos.admin_2fa')

_is_staff = user_passes_test(lambda u: u.is_active and u.is_staff)


# ─── Middleware ─────────────────────────────────────────────────────────────

class Admin2FAMiddleware:
    """
    Bloquea acceso al admin a usuarios staff que ya están autenticados en
    Django pero aún no completaron el segundo factor (TOTP).

    - Solo aplica si el usuario tiene AdminTOTP con totp_activo=True.
    - Si no tiene 2FA configurado, deja pasar (para que puedan hacer el setup).
    - Exenta las rutas /2fa/ del propio admin 2FA para evitar loop.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        admin_base = '/' + settings.ADMIN_URL_PATH

        if request.path.startswith(admin_base):
            user = getattr(request, 'user', None)
            if user and user.is_authenticated and user.is_staff:
                # No bloquear las rutas de 2FA ni el login mismo
                admin_2fa_base = admin_base + '2fa/'
                admin_login    = admin_base + 'login/'
                admin_logout   = admin_base + 'logout/'

                if not (
                    request.path.startswith(admin_2fa_base)
                    or request.path == admin_login
                    or request.path == admin_logout
                ):
                    if not request.session.get('admin_2fa_ok'):
                        try:
                            totp_profile = user.totp
                            if totp_profile.totp_activo:
                                return redirect(admin_2fa_base + 'verify/')
                        except Exception:
                            # AdminTOTP no existe → no hay 2FA configurado → dejar pasar
                            pass

        return self.get_response(request)


# ─── Vistas ─────────────────────────────────────────────────────────────────

@login_required
def setup_view(request):
    """Muestra QR y permite activar TOTP para el admin."""
    from django.conf import settings
    from correos.models import AdminTOTP
    from correos import totp as totp_helpers

    if not (request.user.is_active and request.user.is_staff):
        return HttpResponseForbidden()

    admin_base = '/' + settings.ADMIN_URL_PATH
    profile, _ = AdminTOTP.objects.get_or_create(user=request.user)

    error = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'generar':
            profile.totp_secret = totp_helpers.generar_secret()
            profile.totp_activo = False
            profile.save()
        elif action == 'activar':
            codigo = request.POST.get('codigo', '').strip()
            if not profile.totp_secret:
                error = 'Primero generá el QR.'
            elif totp_helpers.verificar(profile, codigo):
                codes_planos = totp_helpers.generar_recovery_codes_planos()
                profile.recovery_codes_hash = totp_helpers.hashear_codes(codes_planos)
                profile.totp_activo = True
                profile.save()
                request.session['admin_2fa_ok'] = True
                request.session['admin_2fa_setup_codes'] = codes_planos
                return redirect(admin_base + '2fa/recovery/')
            else:
                error = 'Código incorrecto o expirado.'

    qr_svg = None
    if profile.totp_secret:
        qr_svg = totp_helpers.generar_qr_svg(profile.totp_secret, request.user.email, 'RSP Admin')

    ctx = {
        'profile':    profile,
        'qr_svg':     qr_svg,
        'identidad':  request.user.email,
        'error':      error,
        'admin_base': admin_base,
    }
    return render(request, 'admin_2fa/setup.html', ctx)


@login_required
def verify_view(request):
    """Verifica TOTP o recovery code antes de dar acceso al admin."""
    from django.conf import settings
    from correos.models import AdminTOTP
    from correos import totp as totp_helpers

    if not (request.user.is_active and request.user.is_staff):
        return HttpResponseForbidden()

    admin_base = '/' + settings.ADMIN_URL_PATH

    # Si ya verificó, redirigir al admin
    if request.session.get('admin_2fa_ok'):
        return redirect(admin_base)

    try:
        profile = request.user.totp
    except AdminTOTP.DoesNotExist:
        # Sin 2FA configurado → ir a setup
        return redirect(admin_base + '2fa/setup/')

    modo = request.GET.get('modo', 'totp')
    error = None
    recovery_count = len([h for h in profile.recovery_codes_hash if h])

    if request.method == 'POST':
        modo = request.POST.get('modo', 'totp')
        codigo = request.POST.get('codigo', '').strip()

        if modo == 'recovery':
            ok, updated = totp_helpers.verificar_recovery(profile, codigo)
            if ok:
                profile.recovery_codes_hash = updated
                profile.ultima_2fa_ok = timezone.now()
                profile.save()
                request.session['admin_2fa_ok'] = True
                return redirect(admin_base)
            else:
                error = 'Código de recuperación inválido o ya usado.'
        else:
            if totp_helpers.verificar(profile, codigo):
                profile.ultima_2fa_ok = timezone.now()
                profile.save()
                request.session['admin_2fa_ok'] = True
                return redirect(admin_base)
            else:
                error = 'Código incorrecto o expirado.'

    ctx = {
        'modo':           modo,
        'error':          error,
        'recovery_count': recovery_count,
        'admin_base':     admin_base,
    }
    return render(request, 'admin_2fa/verify.html', ctx)


@login_required
def recovery_codes_view(request):
    """Muestra los recovery codes recién generados (solo una vez)."""
    from django.conf import settings

    if not (request.user.is_active and request.user.is_staff):
        return HttpResponseForbidden()

    admin_base = '/' + settings.ADMIN_URL_PATH
    codes = request.session.pop('admin_2fa_setup_codes', None)
    if not codes:
        return redirect(admin_base + '2fa/setup/')

    return render(request, 'admin_2fa/recovery_codes.html', {
        'codes': codes,
        'admin_base': admin_base,
    })


@login_required
def recovery_confirm_view(request):
    """Marca que el usuario guardó sus recovery codes."""
    from django.conf import settings
    if request.method == 'POST':
        return redirect('/' + settings.ADMIN_URL_PATH)
    return redirect('/' + settings.ADMIN_URL_PATH + '2fa/recovery/')


@login_required
def recovery_pdf_view(request):
    """Descarga PDF con los recovery codes."""
    from django.conf import settings
    from correos.models import AdminTOTP

    if not (request.user.is_active and request.user.is_staff):
        return HttpResponseForbidden()

    codes = request.session.get('admin_2fa_setup_codes', [])
    if not codes:
        return redirect('/' + settings.ADMIN_URL_PATH + '2fa/recovery/')

    try:
        from reportlab.pdfgen import canvas as rl_canvas
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf)
        c.setFont('Helvetica-Bold', 16)
        c.drawString(72, 750, 'RSP Admin — Códigos de recuperación 2FA')
        c.setFont('Helvetica', 12)
        c.drawString(72, 720, f'Usuario: {request.user.email}')
        c.drawString(72, 700, 'Guardalos en un lugar seguro. Cada código sirve una sola vez.')
        c.setFont('Courier-Bold', 14)
        y = 660
        for code in codes:
            c.drawString(72, y, code)
            y -= 24
        c.save()
        buf.seek(0)
        return HttpResponse(buf.read(), content_type='application/pdf',
                            headers={'Content-Disposition': 'attachment; filename="recovery_codes.pdf"'})
    except ImportError:
        return HttpResponse('reportlab no instalado', status=500)


# ─── URLs ───────────────────────────────────────────────────────────────────

urlpatterns = [
    path('setup/',              setup_view,            name='admin_2fa_setup'),
    path('verify/',             verify_view,           name='admin_2fa_verify'),
    path('recovery/',           recovery_codes_view,   name='admin_2fa_recovery'),
    path('recovery/confirmar/', recovery_confirm_view, name='admin_2fa_recovery_confirmar'),
    path('recovery/pdf/',       recovery_pdf_view,     name='admin_2fa_recovery_pdf'),
]
