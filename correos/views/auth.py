from ._base import (
    _enviar_alerta_admin, _get_ip, _ip_in_trusted, _ua,
    portal_login_required, _audit, _usuario_actual, _buzon_actual,
    _rl_key, _rl_intento, _rl_bloqueado, _log_intento,
    logger,
    PRE_2FA_TTL, REMEMBER_ME_AGE_DAYS, RE_2FA_AFTER_DAYS,
    LOCKOUT_THRESHOLD, LOCKOUT_DURACION_MIN,
    ALERTA_LOCKOUT_THROTTLE_SEG, ALERTA_FAILS_GLOBAL_THRESHOLD,
    ALERTA_FAILS_GLOBAL_VENTANA_S,
)
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
import time

from taller.anti_bot import verify_turnstile
from .. import captcha
from .. import totp as totp_helpers
from ..models import (
    Buzon, EventoAuditoria, IntentoLogin, UsuarioPortal, hash_ip,
)

# ─── Vistas públicas ───────────────────────────────────────────────────────
def landing_view(request):
    if request.session.get('usuario_email'):
        return redirect('escritorio')
    return render(request, 'correos/landing.html')


def healthz_view(request):
    """
    Healthcheck para Coolify / Cloudflare / monitoring externos.
    Responde 200 'ok' sin tocar BD para que sea rapidísimo y no
    consuma recursos en cada chequeo.
    """
    from django.http import HttpResponse
    return HttpResponse('ok', content_type='text/plain')


def para_talleres_view(request):
    """
    Página comercial pública: presenta el producto a otros talleres /
    plantas RT que podrían contratarlo. No toca BD, render estático.
    """
    return render(request, 'correos/para_talleres.html')


def sla_view(request):
    """
    SLA / términos de servicio del producto. Para clientes activos.
    Página estática pública.
    """
    return render(request, 'correos/sla.html')


def privacidad_view(request):
    """
    Política de privacidad y cookies. Página estática pública, sin BD.
    Linkeada desde el banner de cookies y desde el footer del landing.
    """
    return render(request, 'correos/privacidad.html')


# ─── Login ─────────────────────────────────────────────────────────────────
ERROR_GENERICO = 'No fue posible iniciar sesión. Verifica tus datos e intenta de nuevo.'


@never_cache
@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.session.get('usuario_email'):
        return redirect('escritorio')

    ip_h = hash_ip(_get_ip(request))

    # Helper: contexto base para renderizar el login (siempre incluye site key).
    def _ctx(extra=None):
        ctx = {
            'turnstile_site_key': getattr(settings, 'TURNSTILE_SITE_KEY', ''),
            'page_loaded_at':     int(time.time() * 1000),
        }
        if extra:
            ctx.update(extra)
        return ctx

    # ─── Rate limit a nivel app (la otra capa la pone Cloudflare) ─────────
    if _rl_bloqueado(ip_h):
        _log_intento(request, ip_h, '', motivo='throttled', exito=False)
        messages.error(request, 'Demasiados intentos. Espera unos minutos antes de volver a intentar.')
        return render(request, 'correos/login.html', _ctx(), status=429)

    if request.method == 'GET':
        return render(request, 'correos/login.html', _ctx())

    # ─── POST ─────────────────────────────────────────────────────────────
    email = (request.POST.get('email') or '').strip().lower()
    password = request.POST.get('password') or ''
    honeypot = (request.POST.get('website') or '').strip()      # campo trampa
    page_loaded_at = request.POST.get('page_loaded_at') or '0'
    cf_token = request.POST.get('cf-turnstile-response') or ''

    try:
        tiempo_ms = max(0, int(time.time() * 1000) - int(page_loaded_at))
    except (TypeError, ValueError):
        tiempo_ms = 0

    # Función helper para fallar con respuesta UNIFORME (anti-enumeración)
    def fallo(motivo: str):
        _rl_intento(ip_h, exito=False)
        _log_intento(request, ip_h, email, motivo=motivo, exito=False,
                     tiempo_ms=tiempo_ms, honeypot=bool(honeypot))

        # Detección de ataque global: si el contador acumulado de fallos
        # cruza umbral en la ventana de ALERTA_FAILS_GLOBAL_VENTANA_S, manda
        # alerta UNA vez (throttle de 1h en _enviar_alerta_admin).
        try:
            n_global = cache.get('login_fails_global', 0) + 1
            cache.set('login_fails_global', n_global, ALERTA_FAILS_GLOBAL_VENTANA_S)
            if n_global == ALERTA_FAILS_GLOBAL_THRESHOLD:
                _enviar_alerta_admin(
                    asunto=f'Posible ataque distribuido — {n_global} fallos de login en {ALERTA_FAILS_GLOBAL_VENTANA_S//60} min',
                    body=(
                        f'Detectados {n_global} intentos fallidos de login en '
                        f'los últimos {ALERTA_FAILS_GLOBAL_VENTANA_S // 60} minutos. '
                        f'Esto puede indicar un ataque distribuido (botnet rotando '
                        f'IPs para evitar el rate-limit per-IP).\n\n'
                        f'Acciones sugeridas:\n'
                        f'  1. Revisá IntentoLogin recientes en el admin para ver '
                        f'patrones (mismas IPs? mismos emails? user-agents raros?).\n'
                        f'  2. Si es un ataque real, considerá habilitar Cloudflare '
                        f'Under Attack Mode desde el dashboard.\n'
                        f'  3. Verificá que las cuentas críticas no estén comprometidas.\n\n'
                        f'Próxima alerta de este tipo: en máx 1 hora (throttle).'
                    ),
                    key_throttle='global_attack',
                )
        except Exception:
            logger.exception('Fallo en alerta global (no bloquea login)')

        messages.error(request, ERROR_GENERICO)
        return render(request, 'correos/login.html',
                      _ctx({'last_email': email[:254]}), status=400)

    # 1. Honeypot — bots tienden a rellenar TODO. Humanos no ven el campo.
    if honeypot:
        return fallo('honeypot')

    # 2. Validación básica de email + presencia de password.
    if not email or '@' not in email or len(email) > 254 or not password:
        return fallo('email_invalido')

    # 3. Cloudflare Turnstile — verificación server-side del token.
    #    En dev sin TURNSTILE_SECRET_KEY, verify_turnstile devuelve True.
    if not verify_turnstile(cf_token, ip=_get_ip(request)):
        return fallo('captcha_fail')

    # 4. Usuario existe + activo + password correcto.
    #    Hacemos check_password contra un hash dummy si el usuario no existe
    #    para que el tiempo de respuesta sea similar (anti-timing-enumeration).
    try:
        usuario = UsuarioPortal.objects.get(email=email)

        # 4.a Lockout per-usuario (anti brute-force con botnet rotando IPs).
        #     Igual hace check_password (timing-safe) antes de devolver el
        #     error genérico — no filtramos "esta cuenta está bloqueada".
        if usuario.esta_bloqueado():
            usuario.check_password(password)
            return fallo('usuario_bloqueado')

        if not usuario.activo:
            usuario.check_password(password)
            return fallo('usuario_inactivo')

        if not usuario.check_password(password):
            # Incrementar contador + bloquear si llegó al threshold.
            recien_bloqueado = usuario.registrar_intento_fallido(
                threshold=LOCKOUT_THRESHOLD,
                duracion_min=LOCKOUT_DURACION_MIN,
            )
            if recien_bloqueado:
                logger.warning(
                    'Account lockout activado: email=%s intentos=%d duración=%dmin',
                    email, usuario.intentos_fallidos, LOCKOUT_DURACION_MIN,
                )
                # Audit trail
                try:
                    EventoAuditoria.objects.create(
                        usuario=usuario,
                        accion='login_ok',  # reusamos accion existente; meta diferencia
                        target_tipo='usuarioportal',
                        target_id=usuario.id,
                        meta={
                            'evento': 'lockout_disparado',
                            'intentos': usuario.intentos_fallidos,
                            'duracion_min': LOCKOUT_DURACION_MIN,
                            'bloqueado_hasta': usuario.bloqueado_hasta.isoformat()
                                               if usuario.bloqueado_hasta else None,
                        },
                        ip_hash=ip_h,
                    )
                except Exception:
                    logger.exception('Fallo audit lockout')

                # Email al admin (throttled 1/h por usuario para no spamear).
                _enviar_alerta_admin(
                    asunto=f'Lockout — cuenta {email} bloqueada por brute-force',
                    body=(
                        f'La cuenta {email} fue bloqueada automáticamente por '
                        f'{LOCKOUT_DURACION_MIN} minutos tras {LOCKOUT_THRESHOLD} '
                        f'intentos fallidos consecutivos.\n\n'
                        f'IP hash (parcial): {ip_h[:12]}\n'
                        f'User-Agent: {_ua(request)[:200]}\n'
                        f'Bloqueado hasta: {usuario.bloqueado_hasta:%Y-%m-%d %H:%M:%S}\n\n'
                        f'Si fue intencional (alguien probando), ignorá este aviso. '
                        f'Si NO reconocés actividad, revisá los IntentoLogin '
                        f'recientes desde el admin Django.\n\n'
                        f'Para desbloquear manualmente antes del timeout:\n'
                        f'  Admin Django → UsuarioPortal → editar {email} → '
                        f'limpiar bloqueado_hasta + resetear intentos_fallidos a 0.'
                    ),
                    key_throttle=f'lockout:{email}',
                )
            return fallo('password_invalida')
    except UsuarioPortal.DoesNotExist:
        # Run check_password on a known hash for timing parity
        UsuarioPortal(password_hash='pbkdf2_sha256$600000$dummy$dummy').check_password(password)
        return fallo('email_no_lista')

    # 5. Tiene al menos un buzón visible (o es admin → ve todos).
    primer_buzon = usuario.buzones_visibles().first()
    if primer_buzon is None:
        return fallo('buzon_inexist')

    # ─── Password + captcha + buzones OK → pasamos a la fase 2FA ─────────
    # Cycle de session id para evitar fixation, pero NO marcamos la sesión
    # como autenticada todavía: solo dejamos un flag pre-2FA con expiración.
    request.session.cycle_key()
    request.session['pre_2fa_user_id'] = usuario.id
    request.session['pre_2fa_at']      = int(time.time())
    # "Recordarme" elegido en el form → _promover_sesion lo aplicará tras 2FA OK.
    recordarme = (request.POST.get('recordarme') or '').lower() in ('1', 'on', 'true', 'yes')
    request.session['pre_2fa_recordarme'] = recordarme
    _rl_intento(ip_h, exito=True)
    _log_intento(request, ip_h, email, motivo='pwd_ok_2fa_pend', exito=False,
                 tiempo_ms=tiempo_ms)

    if not usuario.totp_activo:
        return redirect('setup_2fa')
    return redirect('verify_2fa')


@require_POST
def logout_view(request):
    """Logout solo por POST (anti-CSRF: nadie puede desloguearte vía <img>)."""
    request.session.flush()
    return redirect('landing')


# ─── 2FA (TOTP) ────────────────────────────────────────────────────────────
def _get_pre_2fa_user(request) -> UsuarioPortal | None:
    """
    Devuelve el UsuarioPortal cuyo password ya pasó pero le falta 2FA.
    Si el flag está caducado o ausente, limpia y devuelve None.
    """
    uid = request.session.get('pre_2fa_user_id')
    started = request.session.get('pre_2fa_at', 0)
    if not uid:
        return None
    try:
        if int(time.time()) - int(started) > PRE_2FA_TTL:
            for k in ('pre_2fa_user_id', 'pre_2fa_at', 'setup_secret'):
                request.session.pop(k, None)
            return None
    except (TypeError, ValueError):
        return None
    try:
        return UsuarioPortal.objects.get(id=uid, activo=True)
    except UsuarioPortal.DoesNotExist:
        return None


def _promover_sesion(request, usuario: UsuarioPortal) -> None:
    """Pasa una sesión pre-2FA a sesión completa (lo que antes hacía login_view)."""
    recordarme = bool(request.session.get('pre_2fa_recordarme'))
    for k in ('pre_2fa_user_id', 'pre_2fa_at', 'pre_2fa_recordarme', 'setup_secret',
              're_2fa_user_id', 're_2fa_at'):
        request.session.pop(k, None)
    usuario.ultimo_login = timezone.now()
    usuario.save(update_fields=['ultimo_login'])
    request.session.cycle_key()
    request.session['usuario_email']    = usuario.email
    request.session['usuario_es_admin'] = usuario.es_admin
    # Marca el momento del último 2FA exitoso (para re-2FA cada RE_2FA_AFTER_DAYS).
    request.session['ultima_2fa_at']    = int(time.time())
    # Aplica la duración de cookie según "recordarme":
    #   - Marcado: 30 días con sliding (SESSION_SAVE_EVERY_REQUEST=True extiende
    #     con cada acción → si el usuario sigue activo no expira nunca).
    #   - Sin marcar: respeta SESSION_COOKIE_AGE global (8h).
    if recordarme:
        request.session.set_expiry(REMEMBER_ME_AGE_DAYS * 24 * 60 * 60)
    else:
        request.session.set_expiry(0)   # 0 = SESSION_COOKIE_AGE default
    primer = usuario.buzones_visibles().first()
    if primer:
        request.session['buzon_actual_id']    = primer.id
        request.session['buzon_actual_email'] = primer.email


@never_cache
@require_http_methods(['GET', 'POST'])
def setup_2fa_view(request):
    """
    Setup obligatorio de TOTP para usuarios sin 2FA configurado.
    GET → muestra QR + secret. POST con código → valida, activa, genera
    recovery codes y promueve la sesión.
    """
    user = _get_pre_2fa_user(request)
    if not user:
        # Fallback: enforcement path → usuario ya tiene sesión completa pero sin 2FA activo
        user = _usuario_actual(request)
    if not user:
        messages.error(request, 'Tu sesión expiró. Inicia sesión de nuevo.')
        return redirect('login')
    if user.totp_activo:
        # Ya configurado → al verify, no al setup
        return redirect('verify_2fa')

    # Generamos un secret nuevo si todavía no hay uno tentativo en la sesión.
    # Vive solo dentro de esta sesión hasta que el usuario confirme.
    secret = request.session.get('setup_secret')
    if not secret:
        secret = totp_helpers.generar_secret()
        request.session['setup_secret'] = secret

    ip_h = hash_ip(_get_ip(request))

    if request.method == 'POST':
        codigo = request.POST.get('codigo') or ''
        if not totp_helpers.verificar_totp(secret, codigo, valid_window=1):
            _log_intento(request, ip_h, user.email, motivo='totp_fail', exito=False)
            messages.error(request, 'Código inválido. Verifica que la hora del teléfono esté sincronizada.')
            return _render_setup(request, user, secret, status=400)

        # OK: activar 2FA y generar recovery codes.
        codes_planos = totp_helpers.generar_recovery_codes_planos()
        user.totp_secret = secret
        user.totp_activo = True
        user.recovery_codes_hash = totp_helpers.hashear_codes(codes_planos)
        user.totp_ultimo_codigo = totp_helpers.normalizar_codigo_totp(codigo)
        user.save(update_fields=[
            'totp_secret', 'totp_activo', 'recovery_codes_hash', 'totp_ultimo_codigo',
        ])
        _log_intento(request, ip_h, user.email, motivo='totp_setup', exito=True)
        _log_intento(request, ip_h, user.email, motivo='totp_ok', exito=True)

        _promover_sesion(request, user)
        # Los códigos van por sesión y se muestran en la próxima vista.
        # Se quedan ahí (con TTL) para que el user pueda descargar PDF, imprimir,
        # o copiar antes de confirmar. Se borran al confirmar o tras 30 min.
        request.session['recovery_codes_a_mostrar']    = codes_planos
        request.session['recovery_codes_a_mostrar_at'] = int(time.time())
        return redirect('mostrar_recovery_codes')

    return _render_setup(request, user, secret)


def _render_setup(request, user, secret, status=200):
    url = totp_helpers.url_otpauth(secret, user.email)
    return render(request, 'correos/2fa_setup.html', {
        'qr_svg':     totp_helpers.qr_svg(url),
        'secret':     secret,
        'user_email': user.email,
    }, status=status)


def _get_re_2fa_user(request) -> UsuarioPortal | None:
    """
    Usuario en flujo de re-verificación: tiene sesión activa (`usuario_email`)
    pero pasaron RE_2FA_AFTER_DAYS desde el último 2FA OK, y portal_login_required
    nos mandó a verify_2fa para refrescarlo. Distinto de `_get_pre_2fa_user`
    (que cubre el flujo post-password antes de la primera 2FA).
    """
    if not request.session.get('re_2fa_user_id'):
        return None
    return _usuario_actual(request)


@never_cache
@require_http_methods(['GET', 'POST'])
def verify_2fa_view(request):
    """
    Verifica el código TOTP (o un recovery code).

    Dos flujos:
      1) Post-login (password OK, falta 2FA): usuario via _get_pre_2fa_user.
      2) Re-verify en sesión larga: usuario via _get_re_2fa_user (sesión completa,
         pero ultima_2fa_at vieja). Tras OK, refresca ultima_2fa_at en sesión.
    """
    user = _get_pre_2fa_user(request)
    es_re_verify = False
    if not user:
        user = _get_re_2fa_user(request)
        es_re_verify = bool(user)
    if not user:
        messages.error(request, 'Tu sesión expiró. Inicia sesión de nuevo.')
        return redirect('login')
    if not user.totp_activo:
        return redirect('setup_2fa')

    ip_h = hash_ip(_get_ip(request))
    if _rl_bloqueado(ip_h):
        _log_intento(request, ip_h, user.email, motivo='throttled', exito=False)
        messages.error(request, 'Demasiados intentos. Espera unos minutos antes de volver a intentar.')
        return render(request, 'correos/2fa_verify.html', {'modo': 'totp'}, status=429)

    if request.method == 'POST':
        modo = (request.POST.get('modo') or 'totp').lower()
        codigo = request.POST.get('codigo') or ''

        if modo == 'recovery':
            ok, nueva_lista = totp_helpers.consumir_recovery_code(
                list(user.recovery_codes_hash or []), codigo,
            )
            if not ok:
                _rl_intento(ip_h, exito=False)
                _log_intento(request, ip_h, user.email, motivo='recovery_inval', exito=False)
                messages.error(request, 'Código de recuperación inválido.')
                return render(request, 'correos/2fa_verify.html', {'modo': 'recovery'}, status=400)
            user.recovery_codes_hash = nueva_lista
            user.save(update_fields=['recovery_codes_hash'])
            _log_intento(request, ip_h, user.email, motivo='recovery_used', exito=True)
        else:
            if not totp_helpers.verificar_totp(
                user.totp_secret, codigo, ultimo_usado=user.totp_ultimo_codigo,
            ):
                _rl_intento(ip_h, exito=False)
                _log_intento(request, ip_h, user.email, motivo='totp_fail', exito=False)
                messages.error(request, 'Código incorrecto.')
                return render(request, 'correos/2fa_verify.html', {'modo': 'totp'}, status=400)
            user.totp_ultimo_codigo = totp_helpers.normalizar_codigo_totp(codigo)
            user.save(update_fields=['totp_ultimo_codigo'])
            _log_intento(request, ip_h, user.email, motivo='totp_ok', exito=True)

        _rl_intento(ip_h, exito=True)
        # Reset del contador de lockout per-usuario tras login + 2FA OK
        user.resetear_intentos()
        if es_re_verify:
            # Re-verify en sesión larga: solo refrescamos ultima_2fa_at, no
            # promovemos (la sesión ya está completa). Limpiamos los flags.
            request.session.pop('re_2fa_user_id', None)
            request.session.pop('re_2fa_at', None)
            request.session['ultima_2fa_at'] = int(time.time())
            return redirect('escritorio')
        _promover_sesion(request, user)
        return redirect('escritorio')

    return render(request, 'correos/2fa_verify.html', {
        'modo':            request.GET.get('modo', 'totp'),
        'recovery_count':  len(user.recovery_codes_hash or []),
    })


RECOVERY_DISPLAY_TTL = 30 * 60     # 30 min de ventana para descargar/imprimir/confirmar


def _codes_de_sesion(request) -> list[str] | None:
    codes = request.session.get('recovery_codes_a_mostrar')
    at    = request.session.get('recovery_codes_a_mostrar_at', 0)
    if not codes:
        return None
    try:
        if int(time.time()) - int(at) > RECOVERY_DISPLAY_TTL:
            request.session.pop('recovery_codes_a_mostrar', None)
            request.session.pop('recovery_codes_a_mostrar_at', None)
            return None
    except (TypeError, ValueError):
        return None
    return list(codes)


@portal_login_required
@never_cache
def mostrar_recovery_codes_view(request):
    """
    Muestra los recovery codes recién generados. NO los borra de la sesión:
    el usuario tiene 30 min para descargarlos en PDF, imprimirlos y
    confirmar con "Listo, los guardé". Tras confirmar (o vencer el TTL)
    se borran de la sesión.
    """
    codes = _codes_de_sesion(request)
    if not codes:
        messages.info(request, 'Tus códigos ya no están disponibles para mostrar. Si los necesitás de nuevo, regenerá desde tu perfil.')
        return redirect('inbox')
    return render(request, 'correos/2fa_recovery_codes.html', {'codes': codes})


@portal_login_required
@require_POST
def confirmar_recovery_codes_view(request):
    """POST 'ya los guardé' → borra los códigos de la sesión y redirige al inbox."""
    request.session.pop('recovery_codes_a_mostrar', None)
    request.session.pop('recovery_codes_a_mostrar_at', None)
    return redirect('inbox')


@portal_login_required
@never_cache
def descargar_recovery_pdf_view(request):
    """Sirve los recovery codes recién generados como PDF descargable."""
    codes = _codes_de_sesion(request)
    if not codes:
        return redirect('inbox')
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    pdf_bytes = totp_helpers.pdf_recovery_codes(codes, usuario.email)
    from django.http import HttpResponse
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = (
        'attachment; filename="recovery_codes_rsp.pdf"'
    )
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp


@portal_login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def regenerar_recovery_codes_view(request):
    """
    Permite al usuario logueado regenerar sus 8 recovery codes.
    Requiere reingreso de password (defensa contra session-hijack).
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    if request.method == 'POST':
        password = request.POST.get('password') or ''
        if not usuario.check_password(password):
            messages.error(request, 'Contraseña incorrecta.')
            return render(request, 'correos/2fa_regenerar_codes.html', status=400)
        codes_planos = totp_helpers.generar_recovery_codes_planos()
        usuario.recovery_codes_hash = totp_helpers.hashear_codes(codes_planos)
        usuario.save(update_fields=['recovery_codes_hash'])
        request.session['recovery_codes_a_mostrar']    = codes_planos
        request.session['recovery_codes_a_mostrar_at'] = int(time.time())
        return redirect('mostrar_recovery_codes')

    return render(request, 'correos/2fa_regenerar_codes.html')


@require_http_methods(['GET'])
def captcha_regenerar(request):
    """Endpoint AJAX para refrescar el challenge sin recargar la página."""
    return JsonResponse(captcha.generar_challenge())


@portal_login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def cambiar_password_view(request):
    """
    Permite al usuario logueado cambiar su propia contraseña.
    Requiere conocer la actual + cumplir AUTH_PASSWORD_VALIDATORS.
    """
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjValError

    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    if request.method == 'GET':
        return render(request, 'correos/cambiar_password.html')

    actual = request.POST.get('actual') or ''
    nueva  = request.POST.get('nueva') or ''
    nueva2 = request.POST.get('nueva2') or ''

    errores = []

    if not usuario.check_password(actual):
        errores.append('La contraseña actual no es correcta.')
    if nueva != nueva2:
        errores.append('Las contraseñas nuevas no coinciden.')
    if nueva == actual and nueva:
        errores.append('La nueva contraseña debe ser distinta de la actual.')

    if not errores:
        try:
            validate_password(nueva, user=usuario)
        except DjValError as e:
            errores.extend(e.messages)

    if errores:
        for err in errores:
            messages.error(request, err)
        return render(request, 'correos/cambiar_password.html', status=400)

    usuario.set_password(nueva)
    usuario.save(update_fields=['password_hash'])
    # Rotar sesión por buenas prácticas tras cambio sensible
    request.session.cycle_key()
    messages.success(request, 'Contraseña actualizada correctamente.')
    return redirect('inbox')

