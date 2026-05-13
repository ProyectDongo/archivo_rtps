import logging
import re
import time
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncDate, TruncMonth
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from . import captcha, totp as totp_helpers
from .models import (
    Adjunto, Archivo, ArchivoComparticion, ArchivoVinculo, BorradorAdjunto,
    BorradorCorreo, Buzon, CategoriaTema, Correo, CorreoEnviado, CorreoLeido,
    CorreoSnooze, Etiqueta, EventoAuditoria, IntentoLogin, ReenvioCorreo,
    UserDesktopPrefs, UsuarioPortal, hash_ip,
)
from .throttle import throttle_user
from taller.anti_bot import verify_turnstile

logger = logging.getLogger('correos.views')


# Tiempo máximo entre password OK y completar 2FA (segundos).
PRE_2FA_TTL = 5 * 60

# Account lockout (anti brute-force per-usuario)
LOCKOUT_THRESHOLD     = 5      # fallos consecutivos antes de bloquear
LOCKOUT_DURACION_MIN  = 30     # cuánto dura el bloqueo

# Alerta al admin: thresholds para mandar email de "actividad sospechosa".
# Se cachea para no spamear (un solo email por dirección por hora).
ALERTA_LOCKOUT_THROTTLE_SEG    = 60 * 60       # 1h entre alertas del mismo evento
ALERTA_FAILS_GLOBAL_THRESHOLD  = 20            # fallos totales/IP-distintas en ventana
ALERTA_FAILS_GLOBAL_VENTANA_S  = 10 * 60       # ventana de 10 min


def _enviar_alerta_admin(asunto: str, body: str, key_throttle: str) -> None:
    """
    Manda email al admin (settings.PORTAL_ADMIN_EMAIL) si no se mandó otra
    alerta con la misma key_throttle en ALERTA_LOCKOUT_THROTTLE_SEG.

    Failure-mode: si SMTP está caído o no está configurado, NO bloquea el
    flujo. La info ya está en el log + EventoAuditoria.
    """
    try:
        if cache.get(f'alerta_admin:{key_throttle}'):
            return
        cache.set(f'alerta_admin:{key_throttle}', 1, ALERTA_LOCKOUT_THROTTLE_SEG)

        from django.core.mail import send_mail
        admin_to = getattr(settings, 'PORTAL_ADMIN_EMAIL', '')
        if not admin_to:
            return
        send_mail(
            subject=f'[Pietramonte · Seguridad] {asunto}',
            message=body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[admin_to],
            fail_silently=True,
        )
    except Exception:
        logger.exception('Fallo enviar alerta admin (no bloquea login flow)')


# ─── Helpers ───────────────────────────────────────────────────────────────
def _get_ip(request) -> str:
    """
    Devuelve la IP del cliente. Si la conexión llega de un proxy de confianza
    (settings.TRUSTED_PROXIES), respetamos X-Forwarded-For; sino caemos a
    REMOTE_ADDR. Esto evita spoofing si alguna vez alguien expone Django sin
    Cloudflare delante (el atacante podría setear XFF y burlar el rate-limit).

    En Coolify+Tunnel, TRUSTED_PROXIES debe incluir la red interna de Docker
    (ej. '172.17.0.0/16') o IP del tunnel para que XFF sea respetado.
    Sin TRUSTED_PROXIES seteado, modo conservador: solo REMOTE_ADDR.
    """
    remote = request.META.get('REMOTE_ADDR', '')
    trusted = getattr(settings, 'TRUSTED_PROXIES', None) or []
    if not trusted:
        return remote

    if _ip_in_trusted(remote, trusted):
        fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if fwd:
            # Tomamos el primer IP de la cadena, que es el cliente original.
            return fwd.split(',')[0].strip()
    return remote


def _ip_in_trusted(ip: str, trusted_list) -> bool:
    """¿La IP `ip` está en algún CIDR / IP literal de `trusted_list`?"""
    if not ip:
        return False
    import ipaddress
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in trusted_list:
        entry = entry.strip()
        if not entry:
            continue
        try:
            if '/' in entry:
                if ip_obj in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                if ip_obj == ipaddress.ip_address(entry):
                    return True
        except ValueError:
            continue
    return False


def _ua(request) -> str:
    return (request.META.get('HTTP_USER_AGENT') or '')[:500]


def portal_login_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('usuario_email'):
            return redirect('login')
        return view(request, *args, **kwargs)
    return wrapper


# ─── Audit log helper ──────────────────────────────────────────────────────
def _audit(request, accion: str, target_tipo: str = '', target_id: int = None, **meta):
    """
    Emite un evento de auditoría. No bloquea el flujo si falla — la bitácora
    no debe tumbar la operación principal.

    Uso:
        _audit(request, 'snooze', 'correo', correo.id, until=str(until_at))
        _audit(request, 'firma_actualizar', 'buzon', buzon.id)
    """
    try:
        usuario = _usuario_actual(request)
        EventoAuditoria.objects.create(
            usuario=usuario,
            accion=accion,
            target_tipo=target_tipo[:20],
            target_id=target_id,
            meta=meta or {},
            ip_hash=hash_ip(_get_ip(request)),
        )
    except Exception:
        logger.warning('Falló insertar EventoAuditoria accion=%s', accion, exc_info=True)


# ─── Helpers de sesión multi-buzón ─────────────────────────────────────────
def _usuario_actual(request) -> UsuarioPortal | None:
    """
    Devuelve el UsuarioPortal de la sesión o None si la sesión es inválida.

    Cachea el resultado en `request._portal_user` para que views + context
    processor + middleware reusen la misma instancia y no peguemos a la BD
    múltiples veces por request. Sentinel `False` distingue "no hay usuario"
    de "todavía no consultado".
    """
    cached = getattr(request, '_portal_user', None)
    if cached is not None:
        return cached or None    # False sentinel → None

    email = request.session.get('usuario_email')
    if not email:
        request._portal_user = False
        return None
    try:
        usuario = UsuarioPortal.objects.get(email=email, activo=True)
    except UsuarioPortal.DoesNotExist:
        request.session.flush()
        request._portal_user = False
        return None
    request._portal_user = usuario
    return usuario


def _buzon_actual(request, usuario: UsuarioPortal) -> Buzon | None:
    """
    Devuelve el Buzón "actualmente seleccionado" para este usuario.
    Si la sesión apunta a uno al que ya no tiene acceso, cae en el primero
    visible. Si no tiene ninguno visible, devuelve None.
    """
    visibles = usuario.buzones_visibles()
    buzon_id = request.session.get('buzon_actual_id')

    if buzon_id:
        try:
            return visibles.get(id=buzon_id)
        except Buzon.DoesNotExist:
            pass     # Lost access → fallback

    # Toma el primero disponible y lo deja como activo
    primero = visibles.first()
    if primero:
        request.session['buzon_actual_id'] = primero.id
        request.session['buzon_actual_email'] = primero.email
    return primero


# ─── Rate limiting (por IP, contra el cache de Django) ─────────────────────
RL_VENTANA_SEG = 15 * 60      # 15 minutos
RL_MAX_FALLOS  = 5            # tras 5 fallos, bloquea


def _rl_key(ip_h: str) -> str:
    return f'rl:login:{ip_h}'


def _rl_intento(ip_h: str, exito: bool):
    """Reinicia el contador en éxito; suma 1 en fallo."""
    if exito:
        cache.delete(_rl_key(ip_h))
        return
    n = cache.get(_rl_key(ip_h), 0) + 1
    cache.set(_rl_key(ip_h), n, RL_VENTANA_SEG)


def _rl_bloqueado(ip_h: str) -> bool:
    return cache.get(_rl_key(ip_h), 0) >= RL_MAX_FALLOS


# ─── Logging de intentos (para ML futuro) ──────────────────────────────────
def _log_intento(request, ip_h: str, email: str, motivo: str, exito: bool,
                 tiempo_ms: int = 0, captcha_cat: str = '', honeypot: bool = False):
    try:
        IntentoLogin.objects.create(
            ip_hash=ip_h,
            user_agent=_ua(request),
            email_intentado=email[:254],
            captcha_categoria=captcha_cat[:30],
            tiempo_ms=max(0, min(tiempo_ms, 10**8)),
            honeypot_lleno=honeypot,
            exito=exito,
            motivo=motivo,
        )
    except Exception:
        # Nunca bloquear el flujo de login por un fallo de logging — pero al
        # menos dejamos rastro para diagnosticar si Postgres está caído o si
        # el modelo cambió bajo nuestros pies.
        logger.warning('No se pudo registrar IntentoLogin motivo=%s', motivo, exc_info=True)


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
    for k in ('pre_2fa_user_id', 'pre_2fa_at', 'setup_secret'):
        request.session.pop(k, None)
    usuario.ultimo_login = timezone.now()
    usuario.save(update_fields=['ultimo_login'])
    request.session.cycle_key()
    request.session['usuario_email']    = usuario.email
    request.session['usuario_es_admin'] = usuario.es_admin
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


@never_cache
@require_http_methods(['GET', 'POST'])
def verify_2fa_view(request):
    """
    Verifica el código TOTP (o un recovery code) tras login con password.
    Misma lógica de rate-limit por IP que login_view.
    """
    user = _get_pre_2fa_user(request)
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
        'attachment; filename="recovery_codes_pietramonte.pdf"'
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


# ─── Vistas autenticadas ───────────────────────────────────────────────────
def _stats_de(buzon: Buzon) -> dict:
    """
    Calcula métricas para el header del inbox (queries optimizadas, una sola pasada).
    """
    qs = buzon.correos.all()
    ahora = timezone.now()
    hace_30d = ahora - timedelta(days=30)
    hace_6m  = ahora - timedelta(days=183)

    total = qs.count()

    # Top 5 remitentes (por dominio o nombre completo, lo más frecuente)
    top = (qs.exclude(remitente='')
             .values('remitente')
             .annotate(n=Count('id'))
             .order_by('-n')[:5])
    top_remitentes = [
        {'remitente': r['remitente'][:60], 'n': r['n']}
        for r in top
    ]

    # Actividad mensual últimos 6 meses (para mini-gráfico)
    mensual = (qs.filter(fecha__gte=hace_6m)
                 .annotate(mes=TruncMonth('fecha'))
                 .values('mes')
                 .annotate(n=Count('id'))
                 .order_by('mes'))
    chart = [(m['mes'], m['n']) for m in mensual if m['mes']]
    chart_max = max((c[1] for c in chart), default=1)

    return {
        'total':             total,
        'recientes_30d':     qs.filter(fecha__gte=hace_30d).count(),
        'con_adjuntos':      qs.filter(tiene_adjunto=True).count(),
        'fecha_mas_reciente': qs.order_by('-fecha').values_list('fecha', flat=True).first(),
        'fecha_mas_antigua':  qs.exclude(fecha__isnull=True).order_by('fecha').values_list('fecha', flat=True).first(),
        'top_remitentes':    top_remitentes,
        'chart':             chart,
        'chart_max':         chart_max,
    }


def _no_leidos_por_buzon(usuario: UsuarioPortal, buzones) -> dict:
    """
    {buzon_id: cantidad de correos NO leídos por este usuario en ese buzón}.
    Hace 2 queries (totales + leídos) y resta — barato incluso con muchos buzones.
    """
    buzones_ids = [b.id for b in buzones]
    if not buzones_ids:
        return {}

    totales = dict(
        Correo.objects.filter(buzon_id__in=buzones_ids)
        .values_list('buzon_id')
        .annotate(n=Count('id'))
        .values_list('buzon_id', 'n')
    )
    leidos = dict(
        CorreoLeido.objects.filter(usuario=usuario, correo__buzon_id__in=buzones_ids)
        .values_list('correo__buzon_id')
        .annotate(n=Count('id'))
        .values_list('correo__buzon_id', 'n')
    )
    return {bid: max(0, totales.get(bid, 0) - leidos.get(bid, 0)) for bid in buzones_ids}


# ─── Parser de búsqueda con operadores ────────────────────────────────────
# Sintaxis tipo Gmail. Operadores soportados:
#   from:foo@bar.com         remitente contiene "foo@bar.com"
#   to:foo@bar.com           destinatario contiene
#   subject:"hola mundo"     asunto contiene (las comillas permiten espacios)
#   has:attachment           solo con adjunto
#   has:no_attachment        solo sin adjunto
#   before:2026-01-01        antes de esa fecha
#   after:2026-01-01         después de esa fecha
#   label:Factura            con etiqueta llamada "Factura" (case-insensitive)
#   is:starred / is:unread / is:read
# El resto del texto se busca como antes (asunto/remitente/cuerpo).
_OPERATOR_RE = re.compile(r'(\w+):("[^"]+"|\S+)')


def _parse_search_query(query: str):
    """Parsea operadores en una query y devuelve (filtros_dict, texto_libre)."""
    filtros = {
        'from':       [],
        'to':         [],
        'subject':    [],
        'label':      [],
        'has_attachment': None,
        'before':     None,
        'after':      None,
        'is_starred': None,
        'is_unread':  None,
    }

    def _replace(m):
        op = m.group(1).lower()
        val = m.group(2).strip('"').strip()
        if not val:
            return ''
        if op == 'from':
            filtros['from'].append(val)
        elif op == 'to':
            filtros['to'].append(val)
        elif op == 'subject':
            filtros['subject'].append(val)
        elif op == 'label':
            filtros['label'].append(val)
        elif op == 'has':
            v = val.lower()
            if v in ('attachment', 'adjunto'):
                filtros['has_attachment'] = True
            elif v in ('no_attachment', 'sin_adjunto'):
                filtros['has_attachment'] = False
            else:
                return m.group(0)   # operador desconocido → tratar como texto
        elif op == 'before':
            filtros['before'] = val[:10]
        elif op == 'after':
            filtros['after'] = val[:10]
        elif op == 'is':
            v = val.lower()
            if v == 'starred':
                filtros['is_starred'] = True
            elif v == 'unread':
                filtros['is_unread'] = True
            elif v == 'read':
                filtros['is_unread'] = False
            else:
                return m.group(0)
        else:
            return m.group(0)
        return ''

    texto_libre = _OPERATOR_RE.sub(_replace, query)
    texto_libre = re.sub(r'\s+', ' ', texto_libre).strip()
    return filtros, texto_libre


@portal_login_required
@throttle_user('inbox', per_minute=120)   # 2/seg sostenido — más que suficiente para navegación humana
@never_cache
def inbox_view(request):
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')
    buzon = _buzon_actual(request, usuario)
    if not buzon:
        request.session.flush()
        messages.error(request, 'No tienes buzones asignados. Contacta al administrador.')
        return redirect('login')

    correos_qs = buzon.correos.all().prefetch_related('etiquetas').annotate(
        is_leido=Exists(
            CorreoLeido.objects.filter(usuario=usuario, correo=OuterRef('pk'))
        ),
        snoozed_until=CorreoSnooze.objects.filter(
            usuario=usuario, correo=OuterRef('pk'), until_at__gt=timezone.now()
        ).values('until_at')[:1],
    )

    # Si NO está pidiendo la vista de pospuestos, ocultamos los snoozed activos.
    ver_pospuestos = request.GET.get('pospuestos') == '1'
    if ver_pospuestos:
        correos_qs = correos_qs.filter(snoozed_until__isnull=False)
    else:
        correos_qs = correos_qs.exclude(
            id__in=CorreoSnooze.objects.filter(
                usuario=usuario, until_at__gt=timezone.now(), correo__buzon=buzon,
            ).values('correo_id')
        )

    # ─── Filtros ─────────────────────────────────────────────────────────
    # Filtro de carpeta (Inbox / Enviados / Otros / Todos). Default = todos.
    carpeta = (request.GET.get('carpeta') or '').strip().lower()
    if carpeta in ('inbox', 'enviados', 'otros'):
        correos_qs = correos_qs.filter(tipo_carpeta=carpeta)
    else:
        carpeta = ''   # normaliza a vacío = "todos"

    # Conteos por carpeta — un solo GROUP BY en vez de 4 queries separadas.
    # Antes: 4 count() = 4 round-trips a postgres. Ahora: 1 query agrupa todo
    # y el total sale de la suma. Usa el índice (buzon, tipo_carpeta, -fecha).
    counts_qs = buzon.correos.values('tipo_carpeta').annotate(n=Count('id'))
    counts_carpeta = {'inbox': 0, 'enviados': 0, 'otros': 0, 'total': 0}
    for row in counts_qs:
        tipo = row['tipo_carpeta']
        n = row['n']
        if tipo in counts_carpeta:
            counts_carpeta[tipo] = n
        counts_carpeta['total'] += n

    query = (request.GET.get('q') or '').strip()[:200]
    op_filtros, texto_libre = _parse_search_query(query) if query else ({}, '')

    if texto_libre:
        correos_qs = correos_qs.filter(
            Q(asunto__icontains=texto_libre) |
            Q(remitente__icontains=texto_libre) |
            Q(cuerpo_texto__icontains=texto_libre)
        )

    # Operadores de búsqueda avanzada (from:, to:, subject:, has:, before:, after:, label:, is:)
    if op_filtros:
        for v in op_filtros.get('from') or []:
            correos_qs = correos_qs.filter(remitente__icontains=v)
        for v in op_filtros.get('to') or []:
            correos_qs = correos_qs.filter(destinatario__icontains=v)
        for v in op_filtros.get('subject') or []:
            correos_qs = correos_qs.filter(asunto__icontains=v)
        for nombre in op_filtros.get('label') or []:
            correos_qs = correos_qs.filter(etiquetas__nombre__iexact=nombre)
        if op_filtros.get('has_attachment') is True:
            correos_qs = correos_qs.filter(tiene_adjunto=True)
        elif op_filtros.get('has_attachment') is False:
            correos_qs = correos_qs.filter(tiene_adjunto=False)
        if op_filtros.get('before'):
            correos_qs = correos_qs.filter(fecha__date__lt=op_filtros['before'])
        if op_filtros.get('after'):
            correos_qs = correos_qs.filter(fecha__date__gt=op_filtros['after'])
        if op_filtros.get('is_starred') is True:
            correos_qs = correos_qs.filter(destacado=True)
        if op_filtros.get('is_unread') is True:
            correos_qs = correos_qs.filter(is_leido=False)
        elif op_filtros.get('is_unread') is False:
            correos_qs = correos_qs.filter(is_leido=True)

    solo_destacados = request.GET.get('destacado') == '1'
    if solo_destacados:
        correos_qs = correos_qs.filter(destacado=True)

    solo_adjuntos = request.GET.get('adjuntos') == '1'
    if solo_adjuntos:
        correos_qs = correos_qs.filter(tiene_adjunto=True)

    solo_no_leidos = request.GET.get('no_leidos') == '1'
    if solo_no_leidos:
        correos_qs = correos_qs.filter(is_leido=False)

    etiqueta_actual = None
    try:
        etiqueta_id = int(request.GET.get('etiqueta') or 0)
        if etiqueta_id:
            etiqueta_actual = buzon.etiquetas.get(id=etiqueta_id)
            correos_qs = correos_qs.filter(etiquetas=etiqueta_actual)
    except (ValueError, Etiqueta.DoesNotExist):
        pass

    # Filtro por rango de fechas (YYYY-MM-DD).
    fecha_desde = (request.GET.get('desde') or '').strip()[:10]
    fecha_hasta = (request.GET.get('hasta') or '').strip()[:10]
    if fecha_desde:
        correos_qs = correos_qs.filter(fecha__date__gte=fecha_desde)
    if fecha_hasta:
        correos_qs = correos_qs.filter(fecha__date__lte=fecha_hasta)

    # Orden: 'desc' (default, más reciente arriba) o 'asc' (más antiguo arriba).
    orden = (request.GET.get('orden') or 'desc').lower()
    if orden not in ('asc', 'desc'):
        orden = 'desc'
    correos_qs = correos_qs.order_by('fecha' if orden == 'asc' else '-fecha')

    paginator = Paginator(correos_qs, 50)
    page = paginator.get_page(request.GET.get('page', 1))

    hay_filtros_activos = bool(
        query or solo_destacados or solo_adjuntos or solo_no_leidos or etiqueta_actual
        or fecha_desde or fecha_hasta or carpeta or ver_pospuestos
    )

    visibles = list(usuario.buzones_visibles())
    no_leidos = _no_leidos_por_buzon(usuario, visibles)

    cant_pospuestos = CorreoSnooze.objects.filter(
        usuario=usuario, until_at__gt=timezone.now(), correo__buzon=buzon,
    ).count()
    borradores_recientes = list(
        BorradorCorreo.objects.filter(usuario=usuario)
        .order_by('-actualizado')[:8]
    )
    cant_borradores = BorradorCorreo.objects.filter(usuario=usuario).count()

    return render(request, 'correos/inbox.html', {
        'buzon': buzon,
        'page': page,
        'query': query,
        'total': paginator.count,
        'stats': _stats_de(buzon) if not hay_filtros_activos else None,
        'buzones_visibles': visibles,
        'no_leidos_por_buzon': no_leidos,
        'no_leidos_buzon_actual': no_leidos.get(buzon.id, 0),
        'etiquetas_disponibles': buzon.etiquetas.all().order_by('nombre'),
        'etiqueta_actual': etiqueta_actual,
        'solo_destacados': solo_destacados,
        'solo_adjuntos': solo_adjuntos,
        'solo_no_leidos': solo_no_leidos,
        'fecha_desde': fecha_desde,
        'fecha_hasta': fecha_hasta,
        'orden': orden,
        'carpeta': carpeta,
        'counts_carpeta': counts_carpeta,
        'cant_destacados': buzon.correos.filter(destacado=True).count(),
        'hay_filtros_activos': hay_filtros_activos,
        'ver_pospuestos': ver_pospuestos,
        'cant_pospuestos': cant_pospuestos,
        'borradores_recientes': borradores_recientes,
        'cant_borradores': cant_borradores,
    })


@portal_login_required
@throttle_user('detalle', per_minute=240)   # AJAX preview rebota acá si no es fetch
@never_cache
def detalle_view(request, correo_id):
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')
    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404
    # Si el usuario abrió un correo de OTRO buzón al que tiene acceso,
    # cambia la "vista actual" a ese buzón
    if correo.buzon_id != request.session.get('buzon_actual_id'):
        request.session['buzon_actual_id']    = correo.buzon.id
        request.session['buzon_actual_email'] = correo.buzon.email

    # Marca como leído per-usuario (idempotente).
    CorreoLeido.objects.get_or_create(usuario=usuario, correo=correo)

    # Estado de snooze actual (per-usuario) para el botón Posponer/Pospuesto
    snz = CorreoSnooze.objects.filter(
        usuario=usuario, correo=correo, until_at__gt=timezone.now()
    ).first()
    correo.snoozed_until = snz.until_at if snz else None

    # Hilo de conversación (otros correos del mismo asunto en el mismo buzón)
    thread = _hilo_de(correo)[:20]

    # Archivos del Archivo digital vinculados a este correo
    vinculos = (ArchivoVinculo.objects
                .filter(correo=correo)
                .select_related('archivo', 'vinculado_por')
                .order_by('-creado'))
    # Archivos disponibles para vincular (50 más recientes visibles para el user)
    archivos_para_vincular = (_archivos_visibles_qs(usuario)
                              .filter(eliminado_en__isnull=True)
                              .exclude(id__in=vinculos.values_list('archivo_id', flat=True))
                              .order_by('-creado')[:50])

    return render(request, 'correos/detalle.html', {
        'buzon': correo.buzon,
        'correo': correo,
        'buzones_visibles': usuario.buzones_visibles(),
        'thread': thread,
        'etiquetas_disponibles': correo.buzon.etiquetas.all().order_by('nombre'),
        'archivo_vinculos': vinculos,
        'archivos_para_vincular': archivos_para_vincular,
    })


@portal_login_required
@require_POST
def cambiar_buzon_view(request):
    """
    Cambia el buzón "actualmente seleccionado" del usuario.
    Verifica que tenga acceso. POST-only con CSRF (no se puede gatillar via <img>).
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    try:
        buzon_id = int(request.POST.get('buzon_id') or 0)
    except (TypeError, ValueError):
        return HttpResponseBadRequest('buzon_id inválido')

    try:
        buzon = usuario.buzones_visibles().get(id=buzon_id)
    except Buzon.DoesNotExist:
        raise Http404

    request.session['buzon_actual_id']    = buzon.id
    request.session['buzon_actual_email'] = buzon.email
    return redirect('inbox')


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


# ─── Reenvío de correos al exterior ─────────────────────────────────────────
# Cualquier UsuarioPortal puede reenviar correos de los buzones que ve.
# Rate limit: 30 reenvíos/día normales, 100/día admins. Audit completo en
# `ReenvioCorreo`. From=EMAIL_REENVIO_FROM (típicamente la cuenta interna),
# Reply-To=email del usuario portal que reenvía → respuestas vuelven al equipo.
REENVIO_RL_HORAS    = 24
REENVIO_LIMIT_USER  = 30
REENVIO_LIMIT_ADMIN = 100
REENVIO_MAX_DEST    = 5         # max emails por reenvío
REENVIO_MAX_NOTA    = 2000      # max chars del mensaje extra


def _reenvios_recientes(usuario: UsuarioPortal) -> int:
    """Cantidad de reenvíos del usuario en las últimas REENVIO_RL_HORAS."""
    desde = timezone.now() - timedelta(hours=REENVIO_RL_HORAS)
    return ReenvioCorreo.objects.filter(usuario=usuario, enviado_en__gte=desde).count()


def _parse_destinatarios(raw: str) -> list[str]:
    """Parsea 'a@b.cl, c@d.cl' → ['a@b.cl', 'c@d.cl']. Valida formato. Lanza ValidationError."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    emails = [e.strip() for e in (raw or '').replace(';', ',').split(',') if e.strip()]
    if not emails:
        raise ValidationError('Indicá al menos un destinatario.')
    if len(emails) > REENVIO_MAX_DEST:
        raise ValidationError(f'Máximo {REENVIO_MAX_DEST} destinatarios por reenvío.')
    for e in emails:
        validate_email(e)
    return emails


@portal_login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def reenviar_correo_view(request, correo_id):
    """
    Reenvía un correo del archivo a destinatarios externos.

    GET  → form con destinatarios + mensaje extra opcional.
    POST → valida + rate-limit + envía + loguea ReenvioCorreo + redirige al detalle.
    """
    from django.core.exceptions import ValidationError

    from archivo_pietramonte.email_utils import safe_send

    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404

    limite = REENVIO_LIMIT_ADMIN if usuario.es_admin else REENVIO_LIMIT_USER
    usados = _reenvios_recientes(usuario)
    restantes = max(0, limite - usados)

    if request.method == 'GET':
        return render(request, 'correos/reenviar.html', {
            'correo': correo, 'limite': limite, 'usados': usados, 'restantes': restantes,
        })

    # ─── POST ───────────────────────────────────────────────────────────
    if usados >= limite:
        messages.error(request, f'Llegaste al límite de {limite} reenvíos en {REENVIO_RL_HORAS}h. Esperá unas horas o pedile a un admin que lo haga.')
        return render(request, 'correos/reenviar.html', {
            'correo': correo, 'limite': limite, 'usados': usados, 'restantes': 0,
        }, status=429)

    raw_dest = request.POST.get('destinatarios') or ''
    nota     = (request.POST.get('mensaje_extra') or '')[:REENVIO_MAX_NOTA]

    try:
        destinatarios = _parse_destinatarios(raw_dest)
    except ValidationError as e:
        for msg in e.messages if hasattr(e, 'messages') else [str(e)]:
            messages.error(request, msg)
        return render(request, 'correos/reenviar.html', {
            'correo': correo, 'limite': limite, 'usados': usados, 'restantes': restantes,
            'destinatarios': raw_dest, 'mensaje_extra': nota,
        }, status=400)

    # ─── Adjuntos: re-leer del disco para reattachar ───────────────────
    # Separamos en dos: los que tienen content_id (imágenes inline referenciadas
    # con `cid:xxx` desde el HTML del correo) van como parts MIME inline; el
    # resto va como attachments normales descargables.
    adjuntos_payload: list[tuple[str, bytes, str]] = []
    inline_payload:   list[tuple[str, bytes, str, str]] = []
    for adj in correo.adjuntos.all():
        try:
            with adj.archivo.open('rb') as f:
                content = f.read()
        except Exception:
            # Si el archivo se perdió en disco, seguimos sin ese adjunto
            continue
        mime = adj.mime_type or 'application/octet-stream'
        if adj.content_id and mime.lower().startswith('image/'):
            inline_payload.append((adj.nombre_original, content, mime, adj.content_id))
        else:
            adjuntos_payload.append((adj.nombre_original, content, mime))

    # ─── Send ──────────────────────────────────────────────────────────
    # From = la dirección del BUZÓN (consistente con responder). Antes usaba
    # EMAIL_REENVIO_FROM global → caía en soporte.dongo@gmail.com y se veía
    # raro porque el destinatario externo no entiende qué es esa cuenta.
    # Reply-To = el usuario portal: las respuestas del destinatario externo
    # vuelven al usuario que reenvió, NO al archivo (intencional, distinto
    # de responder).
    asunto = f'Fwd: {correo.asunto or "(sin asunto)"}'
    resultado = safe_send(
        asunto=asunto,
        para=destinatarios,
        template='correos/email/reenvio',
        contexto={
            'correo': correo,
            'buzon': correo.buzon,
            'mensaje_extra': nota,
            'reenviado_por': usuario.email,
            **_brand_email_ctx(),
        },
        from_alias=_from_alias_buzon(correo.buzon),
        reply_to=[usuario.email],
        adjuntos=adjuntos_payload,
        inline_images=inline_payload,
    )

    # ─── Audit log ─────────────────────────────────────────────────────
    ip_h = hash_ip(_get_ip(request))
    ReenvioCorreo.objects.create(
        correo=correo,
        usuario=usuario,
        destinatarios=', '.join(destinatarios),
        mensaje_extra=nota,
        exito=resultado['ok'],
        error_msg=(resultado.get('error') or '')[:500],
        ip_hash=ip_h,
    )

    if resultado['ok']:
        messages.success(request, f'Correo reenviado a: {", ".join(destinatarios)}.')
        return redirect('detalle', correo_id=correo.id)

    messages.error(request, f'No se pudo enviar el correo: {resultado.get("error", "error desconocido")}')
    return render(request, 'correos/reenviar.html', {
        'correo': correo, 'limite': limite, 'usados': usados + 1, 'restantes': max(0, restantes - 1),
        'destinatarios': raw_dest, 'mensaje_extra': nota,
    }, status=500)


# ─── Responder / Responder a todos ──────────────────────────────────────────
# Mismo motor SMTP que reenvío, pero el From es la dirección del BUZÓN (no la
# del usuario portal) y agregamos los headers de threading In-Reply-To/References
# para que Gmail agrupe la conversación. La copia se guarda en BD con
# tipo_carpeta=enviados para que aparezca en la pestaña "Enviados" del buzón.
#
# Setup imprescindible (lo hace el cliente, una vez por alias): "Send mail as"
# en la cuenta Gmail centralizadora para cada email del buzón. Sin esto, Gmail
# rechaza el envío o lo manda con el From de la cuenta centralizadora.
RESP_RL_HORAS    = 24
RESP_LIMIT_USER  = 30
RESP_LIMIT_ADMIN = 100
RESP_MAX_DEST    = 10        # poco más que reenvío porque reply-all suma varios
RESP_MAX_BODY    = 50000     # 50 KB de texto plano del usuario


def _enviados_recientes(usuario: UsuarioPortal) -> int:
    """Cantidad de respuestas/composiciones del usuario en las últimas RESP_RL_HORAS."""
    desde = timezone.now() - timedelta(hours=RESP_RL_HORAS)
    return CorreoEnviado.objects.filter(usuario=usuario, enviado_en__gte=desde).count()


def _brand_email_ctx() -> dict:
    """Variables de marca para los templates de email saliente."""
    return {
        'brand_logo_url':    getattr(settings, 'FIRMA_LOGO_URL', ''),
        'brand_color':       getattr(settings, 'BRAND_PRIMARY_COLOR', '#C80C0F'),
        'brand_company_name': getattr(settings, 'BRAND_COMPANY_NAME', 'Pietramonte Automotriz'),
    }


def _from_alias_buzon(buzon: Buzon) -> str:
    """Construye el From del envío. Usa el nombre del buzón si está, si no solo el email."""
    if buzon.nombre:
        return f'{buzon.nombre} <{buzon.email}>'
    return buzon.email


def _prefill_responder(correo: Correo, modo: str) -> dict:
    """
    Devuelve {to, cc, asunto} pre-llenados para el form de responder.
    modo='todos' incluye en Cc a los demás destinatarios del original
    (excluyendo el buzón mismo y el remitente original).
    """
    from email.utils import getaddresses, parseaddr

    original_from = parseaddr(correo.remitente or '')[1] or correo.remitente or ''
    buzon_email = (correo.buzon.email or '').lower()

    cc_str = ''
    if modo == 'todos' and correo.destinatario:
        addrs = getaddresses([correo.destinatario])
        cc_default = [
            a for _name, a in addrs
            if a and a.lower() != buzon_email and a.lower() != original_from.lower()
        ]
        cc_str = ', '.join(cc_default)

    asunto_default = (correo.asunto or '').strip()
    if asunto_default:
        if not asunto_default.lower().startswith('re:'):
            asunto_default = f'Re: {asunto_default}'
    else:
        asunto_default = 'Re: (sin asunto)'

    return {
        'to':     original_from,
        'cc':     cc_str,
        'asunto': asunto_default,
    }


@portal_login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def responder_correo_view(request, correo_id):
    """
    Responde a un correo del archivo. Modo 'simple' (default) responde solo
    al remitente original; modo 'todos' incluye también a los destinatarios
    originales en Cc.

    GET  ?modo=simple|todos → form pre-llenado
    POST → valida + envía + guarda copia en buzon como 'enviados' + audit
    """
    from email.utils import make_msgid

    from django.core.exceptions import ValidationError

    from archivo_pietramonte.email_utils import safe_send

    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404

    modo = (request.GET.get('modo') or request.POST.get('modo') or 'simple').lower()
    if modo not in ('simple', 'todos'):
        modo = 'simple'

    limite = RESP_LIMIT_ADMIN if usuario.es_admin else RESP_LIMIT_USER
    usados = _enviados_recientes(usuario)
    restantes = max(0, limite - usados)

    if request.method == 'GET':
        prefill = _prefill_responder(correo, modo)
        return render(request, 'correos/responder.html', {
            'correo': correo,
            'modo':   modo,
            'to':     prefill['to'],
            'cc':     prefill['cc'],
            'asunto': prefill['asunto'],
            'cuerpo': '',
            'limite': limite, 'usados': usados, 'restantes': restantes,
        })

    # ─── POST ───────────────────────────────────────────────────────────
    if usados >= limite:
        messages.error(request, f'Llegaste al límite de {limite} envíos en {RESP_RL_HORAS}h. Esperá unas horas o pedile a un admin.')
        return render(request, 'correos/responder.html', {
            'correo': correo, 'modo': modo,
            'to': request.POST.get('to') or '', 'cc': request.POST.get('cc') or '',
            'asunto': request.POST.get('asunto') or '', 'cuerpo': request.POST.get('cuerpo') or '',
            'limite': limite, 'usados': usados, 'restantes': 0,
        }, status=429)

    raw_to     = request.POST.get('to')     or ''
    raw_cc     = request.POST.get('cc')     or ''
    asunto     = (request.POST.get('asunto') or '').strip()[:1000]
    cuerpo     = (request.POST.get('cuerpo') or '')[:RESP_MAX_BODY]

    # Validación de destinatarios (reusamos _parse_destinatarios pero permitimos vacío en cc)
    try:
        to_addrs = _parse_destinatarios(raw_to)
    except ValidationError as e:
        for m in (e.messages if hasattr(e, 'messages') else [str(e)]):
            messages.error(request, f'To: {m}')
        return render(request, 'correos/responder.html', {
            'correo': correo, 'modo': modo,
            'to': raw_to, 'cc': raw_cc, 'asunto': asunto, 'cuerpo': cuerpo,
            'limite': limite, 'usados': usados, 'restantes': restantes,
        }, status=400)

    cc_addrs: list[str] = []
    if raw_cc.strip():
        try:
            cc_addrs = _parse_destinatarios(raw_cc)
        except ValidationError as e:
            for m in (e.messages if hasattr(e, 'messages') else [str(e)]):
                messages.error(request, f'Cc: {m}')
            return render(request, 'correos/responder.html', {
                'correo': correo, 'modo': modo,
                'to': raw_to, 'cc': raw_cc, 'asunto': asunto, 'cuerpo': cuerpo,
                'limite': limite, 'usados': usados, 'restantes': restantes,
            }, status=400)

    if len(to_addrs) + len(cc_addrs) > RESP_MAX_DEST:
        messages.error(request, f'Máximo {RESP_MAX_DEST} destinatarios (To + Cc) por envío.')
        return render(request, 'correos/responder.html', {
            'correo': correo, 'modo': modo,
            'to': raw_to, 'cc': raw_cc, 'asunto': asunto, 'cuerpo': cuerpo,
            'limite': limite, 'usados': usados, 'restantes': restantes,
        }, status=400)

    if not asunto:
        asunto = 'Re: (sin asunto)'

    # ─── Threading headers ─────────────────────────────────────────────
    new_msg_id = make_msgid(domain='pietramonte.cl')
    headers = {'Message-ID': new_msg_id}
    if correo.mensaje_id:
        # In-Reply-To y References apuntan al Message-ID del correo original.
        # Si el original ya estaba en un hilo más largo no tenemos las
        # References anteriores guardadas — Gmail tolera References parcial.
        headers['In-Reply-To'] = correo.mensaje_id
        headers['References']  = correo.mensaje_id

    # ─── Inline images del quote ─────────────────────────────────────────
    # El template embebe correo_original.cuerpo_html con sus refs `cid:xxx`.
    # Si no adjuntamos las inline images del original con sus Content-IDs,
    # el destinatario ve `[cid:xxx]` roto en el quote. Solo metemos las que
    # son imágenes (consistente con render del portal).
    inline_payload: list[tuple[str, bytes, str, str]] = []
    if correo.cuerpo_html:
        for adj in correo.adjuntos.exclude(content_id=''):
            mime = (adj.mime_type or '').lower()
            if not mime.startswith('image/'):
                continue
            try:
                with adj.archivo.open('rb') as f:
                    content = f.read()
            except Exception:
                continue
            inline_payload.append((adj.nombre_original, content, adj.mime_type, adj.content_id))

    # ─── Send ──────────────────────────────────────────────────────────
    resultado = safe_send(
        asunto=asunto,
        para=to_addrs,
        cc=cc_addrs or None,
        template='correos/email/respuesta',
        contexto={
            'correo_original': correo,
            'buzon':           correo.buzon,
            'cuerpo_usuario':  cuerpo,
            'enviado_por':     correo.buzon.email,
            **_brand_email_ctx(),
        },
        from_alias=_from_alias_buzon(correo.buzon),
        # NO Reply-To: queremos que las respuestas vuelvan al BUZÓN (no al
        # usuario portal). Gmail sync IMAP las trae al archivo automáticamente.
        headers=headers,
        inline_images=inline_payload,
    )

    # ─── Guardar copia en BD como 'enviados' (solo si se mandó OK) ────
    sent_correo = None
    if resultado['ok']:
        try:
            sent_correo = Correo.objects.create(
                buzon=correo.buzon,
                tipo_carpeta=Correo.Carpeta.ENVIADOS,
                mensaje_id=new_msg_id[:500],
                remitente=_from_alias_buzon(correo.buzon)[:500],
                destinatario=', '.join(to_addrs + cc_addrs)[:1000],
                asunto=asunto[:1000],
                fecha=timezone.now(),
                cuerpo_texto=cuerpo,
                tiene_adjunto=False,
            )
            # Marcado como leído por el que lo envió (es su propio mensaje)
            CorreoLeido.objects.get_or_create(usuario=usuario, correo=sent_correo)
        except Exception:
            # No bloquear el flujo si el save falla — el envío SMTP ya pasó.
            # Pero el audit log sí lo guardamos abajo (CorreoEnviado), así que
            # tenemos rastro del envío. Logueamos para diagnóstico.
            logger.warning(
                'Envío SMTP OK pero fallo al guardar Correo en pestaña Enviados '
                '(usuario=%s, msg_id=%s)', usuario.email, new_msg_id, exc_info=True,
            )
            sent_correo = None

    # ─── Audit ─────────────────────────────────────────────────────────
    ip_h = hash_ip(_get_ip(request))
    CorreoEnviado.objects.create(
        buzon=correo.buzon,
        usuario=usuario,
        correo_original=correo,
        correo_guardado=sent_correo,
        tipo=(CorreoEnviado.Tipo.RESPONDER_TODOS if modo == 'todos'
              else CorreoEnviado.Tipo.RESPONDER),
        destinatarios=', '.join(to_addrs),
        cc=', '.join(cc_addrs),
        asunto=asunto,
        cuerpo=cuerpo,
        mensaje_id=new_msg_id,
        in_reply_to=(correo.mensaje_id or '')[:500],
        exito=resultado['ok'],
        error_msg=(resultado.get('error') or '')[:500],
        ip_hash=ip_h,
    )

    if resultado['ok']:
        msg_dest = ', '.join(to_addrs)
        if cc_addrs:
            msg_dest += f' (cc: {", ".join(cc_addrs)})'
        messages.success(request, f'Respuesta enviada a {msg_dest}.')
        return redirect('detalle', correo_id=correo.id)

    messages.error(request, f'No se pudo enviar la respuesta: {resultado.get("error", "error desconocido")}')
    return render(request, 'correos/responder.html', {
        'correo': correo, 'modo': modo,
        'to': raw_to, 'cc': raw_cc, 'asunto': asunto, 'cuerpo': cuerpo,
        'limite': limite, 'usados': usados + 1, 'restantes': max(0, restantes - 1),
    }, status=500)


@portal_login_required
@throttle_user('adjunto', per_minute=120)
@never_cache
def adjunto_view(request, adjunto_id):
    """
    Sirve un adjunto al usuario logueado, SOLO si pertenece a un correo
    de uno de SUS buzones visibles.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    adjunto = get_object_or_404(Adjunto, id=adjunto_id)
    if not usuario.puede_ver(adjunto.correo.buzon):
        # 404 (no 403) para no filtrar existencia de adjuntos ajenos
        raise Http404

    try:
        f = adjunto.archivo.open('rb')
    except FileNotFoundError:
        raise Http404('Archivo no encontrado en disco')

    # Inline solo para tipos seguros (PDF, imágenes). El resto fuerza descarga
    # para evitar que un .html adjunto se ejecute como página servida desde
    # nuestro propio dominio (XSS).
    disposition = 'inline' if adjunto.es_seguro_inline else 'attachment'

    response = FileResponse(
        f,
        content_type=adjunto.mime_type or 'application/octet-stream',
        as_attachment=(disposition == 'attachment'),
        filename=adjunto.nombre_original,
    )
    response['X-Content-Type-Options'] = 'nosniff'
    if disposition == 'inline':
        # Permitimos embeber el preview en un <iframe> del MISMO origen (modal
        # de adjuntos en el portal). Settings.X_FRAME_OPTIONS=DENY default
        # bloquearía el iframe sino. CSP relajada lo justo para que el viewer
        # nativo de PDF / players HTML5 funcionen, sin permitir scripts ni
        # recursos externos.
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'none'; "
            "object-src 'self'; "
            "frame-ancestors 'self'"
        )
    else:
        # Attachments forzados: nadie debería embeberlos. Sandbox estricto.
        response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


@portal_login_required
@throttle_user('cid', per_minute=300)   # imagenes inline: muchas por correo, generoso
@never_cache
def adjunto_por_cid_view(request, correo_id, content_id):
    """
    Sirve un adjunto INLINE referenciado por Content-ID. Se usa para
    resolver `cid:xxx` que vienen en `<img src="cid:xxx">` dentro del HTML
    de un correo.

    Restricciones extra vs. adjunto_view:
      - Solo sirve imágenes (mime image/*). Si el cid apunta a otra cosa,
        404 — no queremos que esto sea un canal de descarga oculto.
      - Cache-Control private + max-age 1d: el cid es estable para el correo,
        evitamos pegarle al disk en cada open del preview.
      - Content-Disposition inline siempre (necesario para que el navegador
        lo embeba como <img>).
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404

    # Buscamos el adjunto SOLO dentro de los del propio correo. Eso evita
    # que alguien con acceso a un buzón pueda enumerar cids de otros buzones.
    adjunto = get_object_or_404(Adjunto, correo=correo, content_id=content_id)

    if not (adjunto.mime_type or '').lower().startswith('image/'):
        raise Http404  # cid: solo para imágenes inline

    try:
        f = adjunto.archivo.open('rb')
    except FileNotFoundError:
        raise Http404('Archivo no encontrado en disco')

    response = FileResponse(
        f,
        content_type=adjunto.mime_type or 'application/octet-stream',
        as_attachment=False,
        filename=adjunto.nombre_original,
    )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['Cache-Control'] = 'private, max-age=86400'
    return response


@portal_login_required
@throttle_user('preview', per_minute=240)   # 4/seg — j/k navegación rápida pero no abuso
@never_cache
def correo_preview_view(request, correo_id):
    """
    Devuelve el fragment HTML del cuerpo del correo, para inyectar en el panel
    derecho del split view del inbox vía fetch().
    """
    if not request.headers.get('X-Requested-With') == 'fetch':
        return redirect('detalle', correo_id=correo_id)

    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404

    # Marca como leído per-usuario al abrir el preview (idempotente).
    CorreoLeido.objects.get_or_create(usuario=usuario, correo=correo)

    # Estado snooze actual (per-usuario) para mostrar en el botón.
    snz = CorreoSnooze.objects.filter(
        usuario=usuario, correo=correo, until_at__gt=timezone.now()
    ).first()
    correo.snoozed_until = snz.until_at if snz else None

    # Hilo de conversación (heurística por asunto normalizado + buzón).
    thread = _hilo_de(correo)[:20]

    return render(request, 'correos/_correo_preview.html', {
        'correo': correo,
        'is_leido': True,     # acabamos de marcar; el preview lo refleja
        'thread': thread,
    })


# ─── Prefill JSON para el compose flotante (reply inline) ──────────────────
@portal_login_required
@throttle_user('prefill', per_minute=120)
def correo_prefill_view(request, correo_id):
    """
    GET → devuelve JSON con el prefill {to, cc, asunto} para responder o
    reenviar un correo desde el compose flotante. Acepta ?modo=simple|todos|reenviar.
    """
    usuario, correo = _correo_si_visible(request, correo_id)
    modo = (request.GET.get('modo') or 'simple').lower()

    if modo == 'reenviar':
        asunto = (correo.asunto or '').strip()
        if asunto and not asunto.lower().startswith(('fwd:', 'fw:', 'rv:')):
            asunto = f'Fwd: {asunto}'
        elif not asunto:
            asunto = 'Fwd: (sin asunto)'
        return JsonResponse({
            'modo':    'reenviar',
            'to':      '',
            'cc':      '',
            'asunto':  asunto,
        })

    pref = _prefill_responder(correo, 'todos' if modo == 'todos' else 'simple')
    return JsonResponse({
        'modo':    'responder_todos' if modo == 'todos' else 'responder',
        'to':      pref['to'],
        'cc':      pref['cc'],
        'asunto':  pref['asunto'],
    })


# ─── Threading heurístico (sin tocar el modelo ni el import) ──────────────
_RE_ASUNTO_PREFIJO = re.compile(r'^\s*(re|fwd?|rv|fw)\s*:\s*', re.IGNORECASE)


def _normalizar_asunto(asunto: str) -> str:
    """Quita prefijos Re:/Fwd:/RV:/Fw: repetidos del inicio. Lowercase + trim."""
    s = (asunto or '').strip()
    while True:
        m = _RE_ASUNTO_PREFIJO.match(s)
        if not m:
            break
        s = s[m.end():].strip()
    return s.lower()


def _hilo_de(correo: Correo):
    """
    Devuelve un queryset de correos del MISMO hilo que `correo`, dentro de su
    buzón. Heurística: mismo asunto normalizado (case-insensitive). No toca
    headers porque no los persistimos. Excluye al propio correo.
    Ordenado cronológicamente.
    """
    norm = _normalizar_asunto(correo.asunto)
    if not norm or len(norm) < 4:
        return Correo.objects.none()

    # asunto contiene exactamente la versión normalizada (con o sin prefijos).
    # Postgres collation `icontains` matchea tildes a veces — usamos endswith
    # NO porque los prefijos vienen al inicio. Mejor: igual o termina en " : NORM".
    qs = Correo.objects.filter(buzon=correo.buzon).exclude(id=correo.id)
    qs = qs.filter(
        Q(asunto__iexact=norm) |
        Q(asunto__iendswith=': ' + norm) |
        Q(asunto__iendswith=':' + norm)
    )
    return qs.order_by('fecha').only('id', 'asunto', 'remitente', 'fecha', 'tipo_carpeta')


# ─── AJAX: organización del archivo ────────────────────────────────────────
def _correo_si_visible(request, correo_id):
    """Helper común: devuelve (usuario, correo) o levanta Http404 si no aplica."""
    usuario = _usuario_actual(request)
    if not usuario:
        raise Http404
    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404
    return usuario, correo


@portal_login_required
@require_POST
def toggle_destacado_view(request, correo_id):
    """POST → invierte el flag destacado del correo. Devuelve JSON."""
    _, correo = _correo_si_visible(request, correo_id)
    correo.destacado = not correo.destacado
    correo.save(update_fields=['destacado'])
    return JsonResponse({'destacado': correo.destacado})


@portal_login_required
@require_POST
def toggle_leido_view(request, correo_id):
    """
    POST → invierte el estado leído per-usuario del correo. Devuelve JSON
    con el estado nuevo y el conteo no-leídos del buzón (para refrescar el
    badge del sidebar sin recargar).
    """
    usuario, correo = _correo_si_visible(request, correo_id)
    rec = CorreoLeido.objects.filter(usuario=usuario, correo=correo).first()
    if rec:
        rec.delete()
        is_leido = False
    else:
        CorreoLeido.objects.create(usuario=usuario, correo=correo)
        is_leido = True

    no_leidos_buzon = max(
        0,
        correo.buzon.correos.count() -
        CorreoLeido.objects.filter(usuario=usuario, correo__buzon=correo.buzon).count()
    )
    return JsonResponse({
        'is_leido':         is_leido,
        'buzon_id':         correo.buzon_id,
        'no_leidos_buzon':  no_leidos_buzon,
    })


_SNOOZE_PRESETS = {
    'manana':         {'days': 1, 'hour': 9},
    'esta_tarde':     {'hours': 4},
    'proxima_semana': {'days': 7, 'hour': 9},
}


@portal_login_required
@require_POST
@throttle_user('snooze', per_minute=60)
def snooze_correo_view(request, correo_id):
    """
    POST → posponer un correo per-usuario.

    Parámetros:
      preset       — uno de _SNOOZE_PRESETS (ej "manana"), O
      until        — ISO datetime "YYYY-MM-DDTHH:MM" (input type=datetime-local)

    Si ya existe snooze para (usuario, correo), lo reemplaza.
    """
    from datetime import datetime as dt

    usuario, correo = _correo_si_visible(request, correo_id)

    until_at = None
    preset = (request.POST.get('preset') or '').strip()
    if preset in _SNOOZE_PRESETS:
        spec = _SNOOZE_PRESETS[preset]
        base = timezone.localtime(timezone.now())
        if 'days' in spec:
            base = base + timedelta(days=spec['days'])
        if 'hours' in spec:
            base = base + timedelta(hours=spec['hours'])
        if 'hour' in spec:
            base = base.replace(hour=spec['hour'], minute=0, second=0, microsecond=0)
        until_at = base
    else:
        raw_until = (request.POST.get('until') or '').strip()
        if not raw_until:
            return HttpResponseBadRequest('preset o until requerido')
        try:
            # input datetime-local manda "YYYY-MM-DDTHH:MM" (sin tz)
            naive = dt.fromisoformat(raw_until[:16])
        except ValueError:
            return HttpResponseBadRequest('formato de fecha inválido')
        until_at = timezone.make_aware(naive, timezone.get_current_timezone())

    if until_at <= timezone.now():
        return HttpResponseBadRequest('la fecha tiene que ser futura')

    obj, _ = CorreoSnooze.objects.update_or_create(
        usuario=usuario, correo=correo,
        defaults={'until_at': until_at},
    )
    _audit(request, 'snooze', 'correo', correo.id,
           until=obj.until_at.isoformat(), preset=preset or 'custom')
    return JsonResponse({
        'ok': True,
        'until': obj.until_at.isoformat(),
    })


@portal_login_required
@require_POST
@throttle_user('snooze', per_minute=60)
def unsnooze_correo_view(request, correo_id):
    """POST → cancela el snooze de un correo (vuelve a la bandeja)."""
    usuario, correo = _correo_si_visible(request, correo_id)
    deleted, _ = CorreoSnooze.objects.filter(usuario=usuario, correo=correo).delete()
    if deleted:
        _audit(request, 'unsnooze', 'correo', correo.id)
    return JsonResponse({'ok': True, 'eliminado': deleted > 0})


@portal_login_required
@require_POST
def actualizar_notas_view(request, correo_id):
    """POST notas=... → guarda las notas (max 5000)."""
    _, correo = _correo_si_visible(request, correo_id)
    notas = (request.POST.get('notas') or '')[:5000]
    correo.notas = notas
    correo.save(update_fields=['notas'])
    return JsonResponse({'ok': True, 'notas': notas})


@portal_login_required
@require_POST
def asignar_etiqueta_view(request, correo_id):
    """
    POST etiqueta_id → asigna la etiqueta al correo.
    POST etiqueta_id + accion=quitar → la quita.
    La etiqueta debe pertenecer al MISMO buzón del correo.
    """
    _, correo = _correo_si_visible(request, correo_id)
    try:
        etiqueta_id = int(request.POST.get('etiqueta_id') or 0)
    except (TypeError, ValueError):
        return HttpResponseBadRequest('etiqueta_id inválido')
    accion = request.POST.get('accion', 'asignar')

    try:
        etiqueta = correo.buzon.etiquetas.get(id=etiqueta_id)
    except Etiqueta.DoesNotExist:
        raise Http404

    if accion == 'quitar':
        correo.etiquetas.remove(etiqueta)
        asignada = False
        _audit(request, 'etiqueta_quitar', 'correo', correo.id, etiqueta_id=etiqueta.id)
    else:
        correo.etiquetas.add(etiqueta)
        asignada = True
        _audit(request, 'etiqueta_asignar', 'correo', correo.id, etiqueta_id=etiqueta.id)

    return JsonResponse({
        'asignada': asignada,
        'etiqueta': {'id': etiqueta.id, 'nombre': etiqueta.nombre, 'color': etiqueta.color},
    })


@portal_login_required
@require_POST
def crear_etiqueta_view(request):
    """
    POST nombre=... color=... → crea una etiqueta nueva en el buzón actual
    (el usuario debe tener acceso a ese buzón).
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    buzon = _buzon_actual(request, usuario)
    if not buzon:
        raise Http404

    nombre = (request.POST.get('nombre') or '').strip()[:40]
    color  = (request.POST.get('color') or '#C80C0F').strip()
    if not nombre:
        return HttpResponseBadRequest('nombre requerido')

    # color debe estar en la paleta válida
    paleta_valida = {c for c, _ in Etiqueta.PALETA}
    if color not in paleta_valida:
        color = '#C80C0F'

    etiqueta, creada = Etiqueta.objects.get_or_create(
        buzon=buzon, nombre=nombre,
        defaults={'color': color},
    )
    if not creada and etiqueta.color != color:
        etiqueta.color = color
        etiqueta.save(update_fields=['color'])
    if creada:
        _audit(request, 'etiqueta_crear', 'etiqueta', etiqueta.id,
               nombre=etiqueta.nombre, buzon_id=buzon.id)

    return JsonResponse({
        'creada': creada,
        'etiqueta': {'id': etiqueta.id, 'nombre': etiqueta.nombre, 'color': etiqueta.color},
    })


# ─── Firma del buzón actual (editor en el portal) ─────────────────────────
@portal_login_required
@throttle_user('firma', per_minute=60)
@require_http_methods(['GET', 'POST'])
def firma_view(request):
    """
    GET  → form de edición de la firma del buzón actual + preview en vivo.
    POST → guarda los campos de firma y devuelve a la misma página.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')
    buzon = _buzon_actual(request, usuario)
    if not buzon:
        raise Http404

    if request.method == 'POST':
        buzon.firma_activa = request.POST.get('firma_activa') == '1'
        buzon.firma_nombre        = (request.POST.get('firma_nombre') or '').strip()[:120]
        buzon.firma_cargo         = (request.POST.get('firma_cargo') or '').strip()[:120]
        buzon.firma_telefono      = (request.POST.get('firma_telefono') or '').strip()[:40]
        # Validación de firma_web: aceptamos formato "www.x.cl" o con http(s)://.
        # Rechazamos esquemas peligrosos (javascript:, data:, vbscript:, file:).
        web_raw = (request.POST.get('firma_web') or '').strip()[:120]
        if web_raw:
            web_lower = web_raw.lower()
            esquemas_peligrosos = ('javascript:', 'data:', 'vbscript:', 'file:', 'about:')
            if any(web_lower.startswith(s) for s in esquemas_peligrosos):
                messages.error(request, 'El sitio web tiene un formato no permitido.')
                return render(request, 'correos/firma_edit.html', {'buzon': buzon})
            # Permitimos solo letras/dígitos/puntos/guiones + slash/colon/path.
            # Patrón liberal pero suficiente para descartar texto extraño.
            if not re.match(r'^(https?://)?[a-zA-Z0-9._\-]+(\.[a-zA-Z]{2,})+(/[\w\-./?=&%#:+~]*)?$', web_raw):
                messages.error(request, 'El sitio web tiene un formato inválido. Ej.: www.empresa.cl')
                return render(request, 'correos/firma_edit.html', {'buzon': buzon})
            buzon.firma_web = web_raw
        else:
            buzon.firma_web = ''
        email_v = (request.POST.get('firma_email_visible') or '').strip()
        if email_v:
            from django.core.exceptions import ValidationError
            from django.core.validators import validate_email
            try:
                validate_email(email_v)
                buzon.firma_email_visible = email_v[:254]
            except ValidationError:
                messages.error(request, 'El email visible no tiene un formato válido.')
                return render(request, 'correos/firma_edit.html', {'buzon': buzon})
        else:
            buzon.firma_email_visible = ''
        buzon.save(update_fields=[
            'firma_activa', 'firma_nombre', 'firma_cargo',
            'firma_telefono', 'firma_email_visible', 'firma_web',
        ])
        _audit(request, 'firma_actualizar', 'buzon', buzon.id, activa=buzon.firma_activa)
        messages.success(request, 'Firma guardada. Se aplica desde el próximo correo enviado.')
        return redirect('firma')

    return render(request, 'correos/firma_edit.html', {'buzon': buzon})


# ─── Borradores (drafts) ──────────────────────────────────────────────────
def _borrador_dict(b: BorradorCorreo) -> dict:
    return {
        'id':                b.id,
        'modo':              b.modo,
        'correo_original_id': b.correo_original_id,
        'to':                b.to,
        'cc':                b.cc,
        'asunto':            b.asunto,
        'cuerpo':            b.cuerpo,
        'actualizado':       b.actualizado.isoformat(),
    }


_BORRADOR_CAMPOS_EDITABLES = {'to', 'cc', 'asunto', 'cuerpo', 'modo'}


@portal_login_required
@throttle_user('borradores', per_minute=120)
@require_http_methods(['GET', 'POST'])
def borradores_view(request):
    """
    GET  → lista los borradores del usuario actual.
    POST → crea un borrador nuevo y devuelve {id, ...}.

    Body POST (form-encoded o JSON):
      modo                 (opcional, default 'compose')
      correo_original_id   (opcional, para responder/reenviar)
      to, cc, asunto, cuerpo (todos opcionales — el usuario puede empezar vacío)
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return JsonResponse({'error': 'no_session'}, status=403)

    if request.method == 'GET':
        items = list(
            BorradorCorreo.objects.filter(usuario=usuario)
            .select_related('buzon', 'correo_original')
            .order_by('-actualizado')[:200]
        )
        return JsonResponse({
            'borradores': [_borrador_dict(b) for b in items],
        })

    # POST → crear
    buzon = _buzon_actual(request, usuario)
    if not buzon:
        return JsonResponse({'error': 'no_buzon'}, status=400)

    modo = (request.POST.get('modo') or BorradorCorreo.Modo.COMPOSE).strip()
    if modo not in dict(BorradorCorreo.Modo.choices):
        modo = BorradorCorreo.Modo.COMPOSE

    correo_original = None
    raw_orig = request.POST.get('correo_original_id')
    if raw_orig and str(raw_orig).isdigit():
        try:
            correo_original = Correo.objects.get(id=int(raw_orig))
            if not usuario.puede_ver(correo_original.buzon):
                correo_original = None
        except Correo.DoesNotExist:
            correo_original = None

    b = BorradorCorreo.objects.create(
        usuario=usuario,
        buzon=buzon,
        modo=modo,
        correo_original=correo_original,
        to=(request.POST.get('to') or '')[:5000],
        cc=(request.POST.get('cc') or '')[:5000],
        asunto=(request.POST.get('asunto') or '')[:1000],
        cuerpo=(request.POST.get('cuerpo') or '')[:50000],
    )
    _audit(request, 'borrador_crear', 'borrador', b.id, modo=b.modo, buzon_id=buzon.id)
    return JsonResponse(_borrador_dict(b))


@portal_login_required
@throttle_user('borradores', per_minute=240)
@require_http_methods(['GET', 'POST', 'DELETE'])
def borrador_detalle_view(request, borrador_id):
    """
    GET    → devuelve el borrador.
    POST   → update parcial (autosave). Acepta to/cc/asunto/cuerpo/modo.
    DELETE → descartar borrador.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return JsonResponse({'error': 'no_session'}, status=403)
    b = get_object_or_404(BorradorCorreo, id=borrador_id, usuario=usuario)

    if request.method == 'GET':
        return JsonResponse(_borrador_dict(b))

    if request.method == 'DELETE':
        _audit(request, 'borrador_borrar', 'borrador', b.id)
        b.delete()
        return JsonResponse({'ok': True})

    # POST → autosave
    cambios = []
    for k in _BORRADOR_CAMPOS_EDITABLES:
        if k in request.POST:
            v = request.POST.get(k) or ''
            if k == 'asunto':
                v = v[:1000]
            elif k == 'cuerpo':
                v = v[:50000]
            elif k in ('to', 'cc'):
                v = v[:5000]
            elif k == 'modo':
                if v not in dict(BorradorCorreo.Modo.choices):
                    continue
            setattr(b, k, v)
            cambios.append(k)
    if cambios:
        b.save(update_fields=cambios + ['actualizado'])
    return JsonResponse(_borrador_dict(b))


@portal_login_required
@require_POST
def borrador_adjunto_upload_view(request, borrador_id):
    import mimetypes
    usuario = _usuario_actual(request)
    if not usuario:
        return JsonResponse({'error': 'no_session'}, status=403)
    b = get_object_or_404(BorradorCorreo, id=borrador_id, usuario=usuario)
    archivo = request.FILES.get('file')
    if not archivo:
        return JsonResponse({'error': 'No hay archivo.'}, status=400)
    MAX_FILE = 10 * 1024 * 1024
    MAX_TOTAL = 25 * 1024 * 1024
    if archivo.size > MAX_FILE:
        return JsonResponse({'error': 'El archivo supera los 10 MB.'}, status=400)
    total_actual = sum(a.tamanio for a in b.adjuntos_borrador.all())
    if total_actual + archivo.size > MAX_TOTAL:
        return JsonResponse({'error': 'Los adjuntos superan 25 MB en total.'}, status=400)
    mime, _ = mimetypes.guess_type(archivo.name)
    adj = BorradorAdjunto.objects.create(
        borrador=b,
        nombre_original=archivo.name[:500],
        mime_type=mime or 'application/octet-stream',
        archivo=archivo,
        tamanio=archivo.size,
    )
    return JsonResponse({'id': adj.id, 'nombre': adj.nombre_original, 'tamanio': adj.tamanio})


@portal_login_required
def borrador_adjunto_delete_view(request, borrador_id, adj_id):
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Método no permitido.'}, status=405)
    usuario = _usuario_actual(request)
    if not usuario:
        return JsonResponse({'error': 'no_session'}, status=403)
    b = get_object_or_404(BorradorCorreo, id=borrador_id, usuario=usuario)
    adj = get_object_or_404(BorradorAdjunto, id=adj_id, borrador=b)
    adj.delete()
    return JsonResponse({'ok': True})


@portal_login_required
@require_POST
@throttle_user('enviar', per_minute=20)
def borrador_enviar_view(request, borrador_id):
    """
    POST → toma un borrador, valida, manda el correo (vía safe_send),
    persiste como Correo en Enviados y BORRA el borrador.

    Reusa la misma lógica de validación + envío que compose_view /
    responder_correo_view, pero opera sobre el borrador como fuente de datos.
    """
    from email.utils import make_msgid

    from django.core.exceptions import ValidationError

    from archivo_pietramonte.email_utils import safe_send

    usuario = _usuario_actual(request)
    if not usuario:
        return JsonResponse({'error': 'no_session'}, status=403)
    b = get_object_or_404(BorradorCorreo, id=borrador_id, usuario=usuario)
    buzon = b.buzon

    limite = RESP_LIMIT_ADMIN if usuario.es_admin else RESP_LIMIT_USER
    usados = _enviados_recientes(usuario)
    if usados >= limite:
        return JsonResponse({
            'ok': False,
            'error': f'Llegaste al límite de {limite} envíos en {RESP_RL_HORAS}h.',
        }, status=429)

    # Permitir override en el POST si el usuario editó algo en el último click
    # (el JS puede enviar campos finales en el POST de "enviar" sin pasar por
    # otro autosave previo — atrapamos esos cambios acá).
    to = (request.POST.get('to') or b.to).strip()
    cc = (request.POST.get('cc') or b.cc).strip()
    asunto = (request.POST.get('asunto') or b.asunto).strip()[:1000]
    cuerpo = (request.POST.get('cuerpo') or b.cuerpo)[:RESP_MAX_BODY]

    try:
        to_addrs = _parse_destinatarios(to)
    except ValidationError as e:
        msg = e.messages[0] if hasattr(e, 'messages') and e.messages else str(e)
        return JsonResponse({'ok': False, 'error': f'To: {msg}'}, status=400)

    cc_addrs: list[str] = []
    if cc:
        try:
            cc_addrs = _parse_destinatarios(cc)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') and e.messages else str(e)
            return JsonResponse({'ok': False, 'error': f'Cc: {msg}'}, status=400)

    if len(to_addrs) + len(cc_addrs) > RESP_MAX_DEST:
        return JsonResponse({
            'ok': False,
            'error': f'Máximo {RESP_MAX_DEST} destinatarios (To + Cc) por envío.',
        }, status=400)
    if not asunto:
        return JsonResponse({'ok': False, 'error': 'El asunto no puede estar vacío.'}, status=400)
    if not cuerpo.strip():
        return JsonResponse({'ok': False, 'error': 'El mensaje no puede estar vacío.'}, status=400)

    new_msg_id = make_msgid(domain='pietramonte.cl')
    headers = {'Message-ID': new_msg_id}
    if b.correo_original and b.correo_original.mensaje_id and b.modo in (
        BorradorCorreo.Modo.RESPONDER, BorradorCorreo.Modo.RESPONDER_TODOS
    ):
        headers['In-Reply-To'] = b.correo_original.mensaje_id
        headers['References']  = b.correo_original.mensaje_id

    template = 'correos/email/respuesta' if b.modo in (
        BorradorCorreo.Modo.RESPONDER, BorradorCorreo.Modo.RESPONDER_TODOS
    ) else 'correos/email/compose'

    contexto = {
        'asunto':         asunto,
        'buzon':          buzon,
        'cuerpo_usuario': cuerpo,
        'enviado_por':    buzon.email,
        **_brand_email_ctx(),
    }
    if template == 'correos/email/respuesta' and b.correo_original:
        contexto['correo_original'] = b.correo_original

    # Load draft attachments
    adjuntos_draft = []
    for adj in b.adjuntos_borrador.all():
        try:
            with adj.archivo.open('rb') as f:
                content = f.read()
            adjuntos_draft.append((adj.nombre_original, content, adj.mime_type))
        except Exception:
            pass

    resultado = safe_send(
        asunto=asunto,
        para=to_addrs,
        cc=cc_addrs or None,
        template=template,
        contexto=contexto,
        from_alias=_from_alias_buzon(buzon),
        headers=headers,
        adjuntos=adjuntos_draft or None,
    )

    sent_correo = None
    if resultado['ok']:
        try:
            sent_correo = Correo.objects.create(
                buzon=buzon,
                tipo_carpeta=Correo.Carpeta.ENVIADOS,
                mensaje_id=new_msg_id[:500],
                remitente=_from_alias_buzon(buzon)[:500],
                destinatario=', '.join(to_addrs + cc_addrs)[:1000],
                asunto=asunto[:1000],
                fecha=timezone.now(),
                cuerpo_texto=cuerpo,
                tiene_adjunto=False,
            )
            CorreoLeido.objects.get_or_create(usuario=usuario, correo=sent_correo)
        except Exception:
            logger.warning(
                'Borrador-enviar: SMTP OK pero fallo al guardar Correo (usuario=%s, msg_id=%s)',
                usuario.email, new_msg_id, exc_info=True,
            )

    tipo_audit = {
        BorradorCorreo.Modo.RESPONDER:       CorreoEnviado.Tipo.RESPONDER,
        BorradorCorreo.Modo.RESPONDER_TODOS: CorreoEnviado.Tipo.RESPONDER_TODOS,
        BorradorCorreo.Modo.COMPOSE:         CorreoEnviado.Tipo.COMPOSE,
        BorradorCorreo.Modo.REENVIAR:        CorreoEnviado.Tipo.COMPOSE,
    }.get(b.modo, CorreoEnviado.Tipo.COMPOSE)

    CorreoEnviado.objects.create(
        buzon=buzon,
        usuario=usuario,
        correo_original=b.correo_original,
        correo_guardado=sent_correo,
        tipo=tipo_audit,
        destinatarios=', '.join(to_addrs),
        cc=', '.join(cc_addrs),
        asunto=asunto,
        cuerpo=cuerpo,
        mensaje_id=new_msg_id,
        in_reply_to=(b.correo_original.mensaje_id or '')[:500] if b.correo_original else '',
        exito=resultado['ok'],
        error_msg=(resultado.get('error') or '')[:500],
        ip_hash=hash_ip(_get_ip(request)),
    )

    if resultado['ok']:
        b.delete()
        return JsonResponse({'ok': True, 'enviado_a': to_addrs + cc_addrs})

    return JsonResponse({
        'ok': False,
        'error': resultado.get('error') or 'Error desconocido al enviar.',
    }, status=500)


# ─── Compose: escribir un correo nuevo desde cero ────────────────────────
@portal_login_required
@throttle_user('enviar', per_minute=20)
@never_cache
@require_http_methods(['GET', 'POST'])
def compose_view(request):
    """
    Escribe y envía un correo nuevo (no es respuesta ni reenvío).
    Usa el buzón actual como From. Reusa el mismo rate-limit y validación
    que `responder_correo_view`.
    """
    from email.utils import make_msgid

    from django.core.exceptions import ValidationError

    from archivo_pietramonte.email_utils import safe_send

    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')
    buzon = _buzon_actual(request, usuario)
    if not buzon:
        raise Http404

    limite = RESP_LIMIT_ADMIN if usuario.es_admin else RESP_LIMIT_USER
    usados = _enviados_recientes(usuario)
    restantes = max(0, limite - usados)

    # Pre-attach desde Archivos: ?archivo=N (repetible). Validamos visibilidad
    # con _archivos_visibles_qs; ids inválidos o sin permiso se ignoran silentes.
    def _prefilled_archivos_from_request(req):
        ids_raw = req.GET.getlist('archivo') if req.method == 'GET' else req.POST.getlist('archivo_ids')
        ids = []
        for s in ids_raw[:10]:
            try:
                ids.append(int(s))
            except (TypeError, ValueError):
                continue
        if not ids:
            return []
        return list(
            _archivos_visibles_qs(usuario)
            .filter(id__in=ids, eliminado_en__isnull=True)
            .only('id', 'nombre', 'mime_type', 'tamano_bytes')
        )

    # Pre-fill desde GET (?to=... &asunto=...) — útil para "Escribir a este remitente".
    if request.method == 'GET':
        prefilled = _prefilled_archivos_from_request(request)
        return render(request, 'correos/compose.html', {
            'buzon': buzon,
            'to':     (request.GET.get('to') or '').strip()[:500],
            'cc':     (request.GET.get('cc') or '').strip()[:500],
            'asunto': (request.GET.get('asunto') or '').strip()[:500],
            'cuerpo': '',
            'limite': limite, 'usados': usados, 'restantes': restantes,
            'prefilled_archivos': prefilled,
        })

    # ─── POST ───────────────────────────────────────────────────────────
    prefilled_archivos = _prefilled_archivos_from_request(request)

    if usados >= limite:
        messages.error(request, f'Llegaste al límite de {limite} envíos en {RESP_RL_HORAS}h. Esperá unas horas o pedile a un admin.')
        return render(request, 'correos/compose.html', {
            'buzon': buzon,
            'to': request.POST.get('to') or '', 'cc': request.POST.get('cc') or '',
            'asunto': request.POST.get('asunto') or '', 'cuerpo': request.POST.get('cuerpo') or '',
            'limite': limite, 'usados': usados, 'restantes': 0,
            'prefilled_archivos': prefilled_archivos,
        }, status=429)

    raw_to = request.POST.get('to') or ''
    raw_cc = request.POST.get('cc') or ''
    asunto = (request.POST.get('asunto') or '').strip()[:1000]
    cuerpo = (request.POST.get('cuerpo') or '')[:RESP_MAX_BODY]

    ctx_form = {
        'buzon': buzon,
        'to': raw_to, 'cc': raw_cc, 'asunto': asunto, 'cuerpo': cuerpo,
        'limite': limite, 'usados': usados, 'restantes': restantes,
        'prefilled_archivos': prefilled_archivos,
    }

    try:
        to_addrs = _parse_destinatarios(raw_to)
    except ValidationError as e:
        for m in (e.messages if hasattr(e, 'messages') else [str(e)]):
            messages.error(request, f'To: {m}')
        return render(request, 'correos/compose.html', ctx_form, status=400)

    cc_addrs: list[str] = []
    if raw_cc.strip():
        try:
            cc_addrs = _parse_destinatarios(raw_cc)
        except ValidationError as e:
            for m in (e.messages if hasattr(e, 'messages') else [str(e)]):
                messages.error(request, f'Cc: {m}')
            return render(request, 'correos/compose.html', ctx_form, status=400)

    if len(to_addrs) + len(cc_addrs) > RESP_MAX_DEST:
        messages.error(request, f'Máximo {RESP_MAX_DEST} destinatarios (To + Cc) por envío.')
        return render(request, 'correos/compose.html', ctx_form, status=400)

    if not asunto:
        messages.error(request, 'El asunto no puede estar vacío.')
        return render(request, 'correos/compose.html', ctx_form, status=400)
    if not cuerpo.strip():
        messages.error(request, 'El mensaje no puede estar vacío.')
        return render(request, 'correos/compose.html', ctx_form, status=400)

    # ─── Adjuntos del usuario ───────────────────────────────────────────
    # Limites: 10 archivos, 25 MB total, blocklist de extensiones ejecutables.
    MAX_FILES = 10
    MAX_TOTAL_BYTES = 25 * 1024 * 1024
    BLOCKED_EXT = {
        'exe', 'bat', 'cmd', 'com', 'scr', 'msi', 'vbs', 'js', 'jar',
        'ps1', 'sh', 'app', 'dmg',
    }
    files = request.FILES.getlist('adjuntos')
    total_prefilled = sum((a.tamano_bytes or 0) for a in prefilled_archivos)
    if len(files) + len(prefilled_archivos) > MAX_FILES:
        messages.error(request, f'Máximo {MAX_FILES} archivos por correo (incluyendo los pre-adjuntados desde Archivos).')
        return render(request, 'correos/compose.html', ctx_form, status=400)
    total = sum(f.size for f in files) + total_prefilled
    if total > MAX_TOTAL_BYTES:
        messages.error(request, f'Los adjuntos suman {total // (1024*1024)} MB; máximo 25 MB total.')
        return render(request, 'correos/compose.html', ctx_form, status=400)

    adjuntos_payload: list[tuple[str, bytes, str]] = []
    archivos_para_persistir: list[tuple[str, bytes, str]] = []
    for f in files:
        ext = (f.name.rsplit('.', 1)[-1] if '.' in f.name else '').lower()
        if ext in BLOCKED_EXT:
            messages.error(request, f'Tipo de archivo no permitido: {f.name}')
            return render(request, 'correos/compose.html', ctx_form, status=400)
        content = f.read()
        mime = f.content_type or 'application/octet-stream'
        adjuntos_payload.append((f.name, content, mime))
        archivos_para_persistir.append((f.name, content, mime))

    # Pre-loaded desde Archivos: leemos el FileField del modelo. Si el storage
    # falla (archivo borrado del disco, B2 inalcanzable), abortamos en vez de
    # enviar un correo parcial.
    for arc in prefilled_archivos:
        try:
            arc_full = Archivo.objects.only('archivo', 'nombre', 'mime_type').get(id=arc.id)
            with arc_full.archivo.open('rb') as fh:
                content = fh.read()
        except Exception:
            logger.warning('Compose: no se pudo leer Archivo id=%s para pre-adjuntar', arc.id, exc_info=True)
            messages.error(request, f'No se pudo leer el archivo «{arc.nombre}» del almacenamiento.')
            return render(request, 'correos/compose.html', ctx_form, status=500)
        mime = (arc_full.mime_type or 'application/octet-stream')
        adjuntos_payload.append((arc_full.nombre, content, mime))
        archivos_para_persistir.append((arc_full.nombre, content, mime))

    new_msg_id = make_msgid(domain='pietramonte.cl')
    headers = {'Message-ID': new_msg_id}

    resultado = safe_send(
        asunto=asunto,
        para=to_addrs,
        cc=cc_addrs or None,
        template='correos/email/compose',
        contexto={
            'asunto':         asunto,
            'buzon':          buzon,
            'cuerpo_usuario': cuerpo,
            'enviado_por':    buzon.email,
            **_brand_email_ctx(),
        },
        from_alias=_from_alias_buzon(buzon),
        headers=headers,
        adjuntos=adjuntos_payload or None,
    )

    sent_correo = None
    if resultado['ok']:
        try:
            sent_correo = Correo.objects.create(
                buzon=buzon,
                tipo_carpeta=Correo.Carpeta.ENVIADOS,
                mensaje_id=new_msg_id[:500],
                remitente=_from_alias_buzon(buzon)[:500],
                destinatario=', '.join(to_addrs + cc_addrs)[:1000],
                asunto=asunto[:1000],
                fecha=timezone.now(),
                cuerpo_texto=cuerpo,
                tiene_adjunto=bool(archivos_para_persistir),
            )
            CorreoLeido.objects.get_or_create(usuario=usuario, correo=sent_correo)
            # Persistir adjuntos como rows Adjunto (para que aparezcan en
            # Enviados con el pill 📎 y se puedan re-descargar).
            from django.core.files.base import ContentFile
            for fname, content, mime in archivos_para_persistir:
                try:
                    adj = Adjunto(
                        correo=sent_correo,
                        nombre_original=fname[:300],
                        mime_type=mime[:200],
                        tamano_bytes=len(content),
                    )
                    adj.archivo.save(fname, ContentFile(content), save=False)
                    adj.save()
                except Exception:
                    logger.warning(
                        'Compose: fallo guardando Adjunto %s del correo %s',
                        fname, sent_correo.id, exc_info=True,
                    )
        except Exception:
            logger.warning(
                'Compose SMTP OK pero fallo al guardar Correo en pestaña Enviados '
                '(usuario=%s, msg_id=%s)', usuario.email, new_msg_id, exc_info=True,
            )
            sent_correo = None

    ip_h = hash_ip(_get_ip(request))
    CorreoEnviado.objects.create(
        buzon=buzon,
        usuario=usuario,
        correo_original=None,
        correo_guardado=sent_correo,
        tipo=CorreoEnviado.Tipo.COMPOSE,
        destinatarios=', '.join(to_addrs),
        cc=', '.join(cc_addrs),
        asunto=asunto,
        cuerpo=cuerpo,
        mensaje_id=new_msg_id,
        in_reply_to='',
        exito=resultado['ok'],
        error_msg=(resultado.get('error') or '')[:500],
        ip_hash=ip_h,
    )

    if resultado['ok']:
        msg_dest = ', '.join(to_addrs)
        if cc_addrs:
            msg_dest += f' (cc: {", ".join(cc_addrs)})'
        messages.success(request, f'Correo enviado a {msg_dest}.')
        return redirect('inbox')

    messages.error(request, f'No se pudo enviar el correo: {resultado.get("error", "error desconocido")}')
    ctx_form['usados'] = usados + 1
    ctx_form['restantes'] = max(0, restantes - 1)
    return render(request, 'correos/compose.html', ctx_form, status=500)


# ─── AJAX: acciones masivas (multi-select) ────────────────────────────────
_BULK_ACCIONES_VALIDAS = {
    'leer', 'no_leer', 'destacar', 'no_destacar',
    'asignar_etiqueta', 'quitar_etiqueta',
}


@portal_login_required
@require_POST
@throttle_user('bulk', per_minute=30)
def bulk_acciones_view(request):
    """
    POST → ejecuta una acción sobre varios correos a la vez.

    Parámetros (form-encoded):
      ids          — coma-separados, ej "12,34,56"  (máx 200 ids por request)
      accion       — uno de _BULK_ACCIONES_VALIDAS
      etiqueta_id  — solo para acciones de etiqueta

    Solo aplica a correos del buzón actual del usuario (filtramos en el query).
    Devuelve el conteo afectado y el badge actualizado.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return JsonResponse({'error': 'no_session'}, status=403)

    accion = (request.POST.get('accion') or '').strip()
    if accion not in _BULK_ACCIONES_VALIDAS:
        return HttpResponseBadRequest('accion inválida')

    raw_ids = request.POST.get('ids') or ''
    ids = []
    for tok in raw_ids.split(','):
        tok = tok.strip()
        if tok.isdigit():
            ids.append(int(tok))
        if len(ids) >= 200:
            break
    if not ids:
        return HttpResponseBadRequest('ids requeridos')

    # Filtrar solo correos visibles al usuario.
    if usuario.es_admin:
        qs = Correo.objects.filter(id__in=ids)
    else:
        qs = Correo.objects.filter(id__in=ids, buzon__usuarios=usuario)

    # Acciones de marcado/destacado
    if accion in {'destacar', 'no_destacar'}:
        nuevo = (accion == 'destacar')
        afectados = qs.update(destacado=nuevo)
    elif accion == 'leer':
        # Crear CorreoLeido por cada uno que no exista.
        existentes = set(
            CorreoLeido.objects.filter(usuario=usuario, correo_id__in=qs.values_list('id', flat=True))
            .values_list('correo_id', flat=True)
        )
        afaltar = [c.id for c in qs.only('id') if c.id not in existentes]
        CorreoLeido.objects.bulk_create(
            [CorreoLeido(usuario=usuario, correo_id=cid) for cid in afaltar],
            ignore_conflicts=True,
        )
        afectados = len(afaltar)
    elif accion == 'no_leer':
        afectados = CorreoLeido.objects.filter(
            usuario=usuario, correo_id__in=qs.values_list('id', flat=True)
        ).delete()[0]
    elif accion in {'asignar_etiqueta', 'quitar_etiqueta'}:
        try:
            et_id = int(request.POST.get('etiqueta_id') or 0)
        except (TypeError, ValueError):
            return HttpResponseBadRequest('etiqueta_id inválido')
        # La etiqueta debe pertenecer al mismo buzón de los correos.
        # Filtramos los correos al buzón de la etiqueta para no asignar
        # cross-buzón (sería inválido).
        try:
            etiqueta = Etiqueta.objects.get(id=et_id)
        except Etiqueta.DoesNotExist:
            raise Http404
        if not usuario.puede_ver(etiqueta.buzon):
            raise Http404
        qs = qs.filter(buzon=etiqueta.buzon)
        afectados = 0
        if accion == 'asignar_etiqueta':
            for c in qs:
                c.etiquetas.add(etiqueta)
                afectados += 1
        else:
            for c in qs:
                c.etiquetas.remove(etiqueta)
                afectados += 1
    else:
        afectados = 0

    # Badge no-leídos del buzón actual (mejor esfuerzo — si hay varios buzones
    # involucrados, devolvemos el del primero).
    no_leidos_buzon = None
    primer_buzon_id = qs.values_list('buzon_id', flat=True).first()
    if primer_buzon_id:
        from .models import Buzon
        no_leidos_buzon = max(
            0,
            Buzon.objects.get(id=primer_buzon_id).correos.count() -
            CorreoLeido.objects.filter(usuario=usuario, correo__buzon_id=primer_buzon_id).count()
        )

    # Audit
    _AUDIT_BULK = {
        'leer':              'bulk_leer',
        'no_leer':           'bulk_no_leer',
        'destacar':          'bulk_destacar',
        'no_destacar':       'bulk_destacar',
        'asignar_etiqueta':  'bulk_etiquetar',
        'quitar_etiqueta':   'bulk_etiquetar',
    }
    if afectados and accion in _AUDIT_BULK:
        _audit(request, _AUDIT_BULK[accion], 'correo', None,
               accion_concreta=accion, n=afectados, ids_sample=ids[:10])

    return JsonResponse({
        'ok': True,
        'afectados': afectados,
        'no_leidos_buzon': no_leidos_buzon,
    })


# ═════════════════════════════════════════════════════════════════════════════
# Escritorio — home tipo Windows con dashboard + widgets (Fase 1)
# ═════════════════════════════════════════════════════════════════════════════

ESCRITORIO_CACHE_TTL = 30 * 60     # 30 min — dashboard cachea para evitar
                                   # full-table scans en cada hit. Si cambian
                                   # categorías o llegan correos, el usuario
                                   # ve los cambios en la próxima rotación.
ESCRITORIO_CHART_DIAS = 14         # días del bar chart de ingresos
ESCRITORIO_TEMAS_VENTANA_DIAS = 180  # top temas solo mira últimos 6 meses
                                     # (archivo histórico = scan inviable).


def _esc_stats_buzones(usuario, buzones_visibles):
    """
    Stats agregados: total correos, total adjuntos, contratos (placeholder),
    citas semana (placeholder hasta wiring taller).
    """
    cache_key = f'esc:stats:{usuario.id}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    correos_total = Correo.objects.filter(buzon__in=buzones_visibles).count()
    adjuntos_total = Adjunto.objects.filter(correo__buzon__in=buzones_visibles).count()

    stats = {
        'correos_total':  correos_total,
        'adjuntos_total': adjuntos_total,
        'contratos_total': 0,    # TODO Fase 2: cuando exista el modelo Contrato
        'citas_semana':    0,    # TODO Fase 2: query a taller.Cita
    }
    cache.set(cache_key, stats, ESCRITORIO_CACHE_TTL)
    return stats


def _esc_chart_ingresos(usuario, buzones_visibles, dias=ESCRITORIO_CHART_DIAS):
    """
    Datos del bar chart "Ingresos últimos N días" — correos recibidos +
    archivos subidos por día. Devuelve lista de dicts ordenada de
    antiguo a reciente.
    """
    cache_key = f'esc:chart:{usuario.id}:{dias}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    hoy = timezone.localdate()
    desde = hoy - timedelta(days=dias - 1)

    # Correos por día — agrupados por fecha local
    correos_por_dia = dict(
        Correo.objects
        .filter(buzon__in=buzones_visibles, fecha__date__gte=desde)
        .annotate(dia=TruncDate('fecha'))
        .values_list('dia')
        .annotate(c=Count('id'))
        .values_list('dia', 'c')
    )
    adjuntos_por_dia = dict(
        Adjunto.objects
        .filter(correo__buzon__in=buzones_visibles, creado__date__gte=desde)
        .annotate(dia=TruncDate('creado'))
        .values_list('dia')
        .annotate(c=Count('id'))
        .values_list('dia', 'c')
    )

    serie = []
    max_val = 1
    for i in range(dias):
        d = desde + timedelta(days=i)
        c = correos_por_dia.get(d, 0)
        a = adjuntos_por_dia.get(d, 0)
        max_val = max(max_val, c, a)
        serie.append({'dia': d, 'correos': c, 'archivos': a})

    # Pre-calculamos altura % para que el template solo concatene
    for p in serie:
        p['h_correos']  = round(p['correos']  * 100 / max_val, 1)
        p['h_archivos'] = round(p['archivos'] * 100 / max_val, 1)

    out = {'serie': serie, 'max_val': max_val}
    cache.set(cache_key, out, ESCRITORIO_CACHE_TTL)
    return out


def _esc_top_temas(buzones_visibles, top=5):
    """
    Top N CategoriaTema activas con más correos matcheados por keyword.
    Match case-insensitive SOLO en `asunto` (no cuerpo_texto).

    Optimización 2026-05-11: el query original buscaba en cuerpo_texto
    (campo TextField sin índice) → full-table scan por keyword × 7
    categorías × ~5-20 keywords = ~700 substring searches en 8000+
    correos = ~20s. Solo asunto (campo corto) baja a <1s.

    Trade-off: pierde matches donde la keyword aparece solo en cuerpo.
    Para precisión total a futuro, materializar M2M Correo↔CategoriaTema
    con clasificador nightly (TODO Fase 2).

    Limita además a últimos ESCRITORIO_TEMAS_VENTANA_DIAS (180 días) —
    el archivo histórico no aporta señal al dashboard "qué se está
    hablando ahora" y multiplica el costo.

    Cacheado global (no por usuario) porque el conteo es por buzones
    visibles. Para multi-tenant futuro habría que keyear por tenant.
    """
    buzon_ids = sorted(buzones_visibles.values_list('id', flat=True))
    desde = timezone.now() - timedelta(days=ESCRITORIO_TEMAS_VENTANA_DIAS)
    # v3 = nueva semántica (solo asunto + ventana 180d). El bump invalida
    # caches viejas con conteos sobre cuerpo_texto del comando anterior.
    cache_key = f'esc:temas:v3:{",".join(map(str, buzon_ids))}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    resultado = []
    base_qs = Correo.objects.filter(buzon_id__in=buzon_ids, fecha__gte=desde)
    for cat in CategoriaTema.objects.filter(activa=True).order_by('orden'):
        kws = cat.keywords_lista()
        if not kws:
            continue
        q = Q()
        for kw in kws[:20]:    # cap por sanidad
            q |= Q(asunto__icontains=kw)
        count = base_qs.filter(q).count()
        resultado.append({
            'id':     cat.id,
            'nombre': cat.nombre,
            'color':  cat.color,
            'count':  count,
        })

    # Top N ordenado por count desc
    resultado.sort(key=lambda x: -x['count'])
    resultado = resultado[:top]
    max_n = max((r['count'] for r in resultado), default=1) or 1
    for r in resultado:
        r['pct'] = round(r['count'] * 100 / max_n, 1)

    cache.set(cache_key, resultado, ESCRITORIO_CACHE_TTL)
    return resultado


def _esc_ultimos_correos(usuario, buzones_visibles, n=4):
    """Últimos N correos cualesquiera de los buzones visibles."""
    qs = (Correo.objects
          .filter(buzon__in=buzones_visibles)
          .select_related('buzon')
          .annotate(is_leido=Exists(
              CorreoLeido.objects.filter(usuario=usuario, correo=OuterRef('pk'))
          ))
          .order_by('-fecha')[:n])
    return list(qs)


def _esc_archivos_recientes(buzones_visibles, n=4):
    """Últimos N adjuntos subidos."""
    return list(
        Adjunto.objects
        .filter(correo__buzon__in=buzones_visibles)
        .select_related('correo', 'correo__buzon')
        .order_by('-creado')[:n]
    )


def _esc_kpis_ejecutivos(usuario, buzones_visibles):
    """
    KPIs de operación: recibidos, enviados, tasa respuesta, sin leer,
    pendientes con snooze, hoy.
    """
    cache_key = f'esc:kpis:{usuario.id}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    base = Correo.objects.filter(buzon__in=buzones_visibles)
    total      = base.count()
    recibidos  = base.filter(tipo_carpeta='inbox').count()
    enviados   = base.filter(tipo_carpeta='enviados').count()
    otros      = total - recibidos - enviados
    # Tasa de respuesta = enviados / recibidos (proxy razonable)
    tasa_resp  = round(enviados * 100 / recibidos, 1) if recibidos else 0.0

    # Sin leer (per-usuario)
    no_leidos = base.exclude(
        id__in=CorreoLeido.objects.filter(usuario=usuario).values('correo_id')
    ).count()

    # Snooze activos
    snooze_activos = CorreoSnooze.objects.filter(
        usuario=usuario, until_at__gt=timezone.now()
    ).count()

    out = {
        'total':          total,
        'recibidos':      recibidos,
        'enviados':       enviados,
        'otros':          otros,
        'tasa_respuesta': tasa_resp,
        'no_leidos':      no_leidos,
        'snooze_activos': snooze_activos,
    }
    cache.set(cache_key, out, ESCRITORIO_CACHE_TTL)
    return out


def _esc_volumen_mensual(usuario, buzones_visibles, meses=12):
    """
    Volumen de correos por mes — últimos N meses. Para chart de líneas
    "tendencia histórica del negocio". Usa `fecha` (timestamp del email)
    porque acá sí queremos visión histórica real.
    """
    cache_key = f'esc:volumen_mensual:{usuario.id}:{meses}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    desde = timezone.now() - timedelta(days=meses * 31)
    raw = list(
        Correo.objects
        .filter(buzon__in=buzones_visibles, fecha__gte=desde)
        .annotate(mes=TruncMonth('fecha'))
        .values('mes')
        .annotate(c=Count('id'))
        .order_by('mes')
    )

    # Normalizar: rellenar meses sin datos con 0 para línea continua.
    # Generamos los últimos `meses` meses calendario hacia atrás.
    hoy = timezone.localdate()
    serie = []
    for offset in range(meses - 1, -1, -1):
        # Calcular el mes target (hoy - offset meses)
        y = hoy.year
        m = hoy.month - offset
        while m <= 0:
            m += 12
            y -= 1
        # Match contra el agrupamiento (datetime al inicio del mes)
        valor = 0
        for row in raw:
            if row['mes'] and row['mes'].year == y and row['mes'].month == m:
                valor = row['c']
                break
        serie.append({'year': y, 'month': m, 'c': valor})

    max_val = max((s['c'] for s in serie), default=1) or 1
    # SVG viewBox: 720 wide × 160 tall, datos clamp a 0..120 (eje Y invertido)
    n = max(len(serie) - 1, 1)
    points = []
    for i, s in enumerate(serie):
        s['h'] = round(s['c'] * 100 / max_val, 1)
        s['label'] = f"{s['year']}-{s['month']:02d}"
        s['x_svg'] = round(i * 720 / n, 1)
        # 120 - (% × 1.2) → invertido para SVG (y=0 está arriba)
        s['y_svg'] = round(120 - s['h'] * 1.2, 1)
        points.append(f"{s['x_svg']},{s['y_svg']}")
    out = {
        'serie':      serie,
        'max_val':    max_val,
        'points_str': ' '.join(points),
    }
    cache.set(cache_key, out, ESCRITORIO_CACHE_TTL)
    return out


def _esc_pie_carpetas(usuario, buzones_visibles):
    """
    Distribución por tipo_carpeta para un donut chart.
    Reutiliza los counts de KPIs.
    """
    kpis = _esc_kpis_ejecutivos(usuario, buzones_visibles)
    total = max(kpis['total'], 1)
    slices = [
        {'nombre': 'Recibidos', 'count': kpis['recibidos'],
         'color': '#C80C0F'},
        {'nombre': 'Enviados',  'count': kpis['enviados'],
         'color': '#2563eb'},
        {'nombre': 'Otros',     'count': kpis['otros'],
         'color': '#94a3b8'},
    ]
    # Acumular pct + offset para stroke-dasharray
    acc = 0
    for s in slices:
        s['pct'] = round(s['count'] * 100 / total, 1)
        s['offset_pct'] = acc
        acc += s['pct']
    return {'slices': slices, 'total': total}


def _esc_top_remitentes_externos(usuario, buzones_visibles, top=10):
    """
    Top N remitentes que NO son @pietramonte.cl. Útil para ver quién
    desde afuera te escribe más (clientes recurrentes, bancos, etc).
    """
    cache_key = f'esc:remits:{usuario.id}:{top}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    dominio_propio = '@pietramonte.cl'
    raw = list(
        Correo.objects
        .filter(buzon__in=buzones_visibles, tipo_carpeta='inbox')
        .exclude(remitente__icontains=dominio_propio)
        .exclude(remitente='')
        .values('remitente')
        .annotate(c=Count('id'))
        .order_by('-c')[:top]
    )
    max_c = max((r['c'] for r in raw), default=1) or 1
    for r in raw:
        r['pct'] = round(r['c'] * 100 / max_c, 1)
        # Limpiar formato "Nombre <email>" → mostrar Nombre si lo tiene
        rem = r['remitente']
        if '<' in rem:
            nombre = rem.split('<', 1)[0].strip().strip('"')
            email  = rem.split('<', 1)[1].rstrip('>').strip()
            r['display'] = nombre or email
            r['email']   = email
        else:
            r['display'] = rem
            r['email']   = rem

    cache.set(cache_key, raw, ESCRITORIO_CACHE_TTL)
    return raw


def _esc_heatmap_actividad(usuario, buzones_visibles, dias=90):
    """
    Heatmap día-de-semana × hora-del-día. Últimos N días para tener señal
    sin diluir con histórico antiguo.

    Devuelve una matriz 7×24 (Lun..Dom × 0..23) con counts + max para
    normalizar la opacidad en el template.
    """
    cache_key = f'esc:heatmap:{usuario.id}:{dias}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    desde = timezone.now() - timedelta(days=dias)
    raw = (
        Correo.objects
        .filter(buzon__in=buzones_visibles, fecha__gte=desde)
        .annotate(dow=ExtractIsoWeekDay('fecha'), h=ExtractHour('fecha'))
        .values('dow', 'h')
        .annotate(c=Count('id'))
    )

    # ExtractIsoWeekDay: 1=Lun, 7=Dom (ISO 8601). Justo lo que queremos.
    matriz = [[0] * 24 for _ in range(7)]
    max_c = 1
    for row in raw:
        dow = row['dow']
        h = row['h']
        if dow is None or h is None:
            continue
        dow_idx = max(0, min(6, dow - 1))   # 1..7 → 0..6
        h_idx   = max(0, min(23, h))
        matriz[dow_idx][h_idx] = row['c']
        if row['c'] > max_c:
            max_c = row['c']

    # Pre-calcular opacity por celda (0..1) para el template
    nombres_dow = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
    filas = []
    for d in range(7):
        celdas = []
        for h in range(24):
            c = matriz[d][h]
            opacity = round(c / max_c, 3) if max_c else 0
            celdas.append({'h': h, 'count': c, 'opacity': opacity})
        filas.append({'dow_label': nombres_dow[d], 'celdas': celdas})

    out = {'filas': filas, 'max_count': max_c}
    cache.set(cache_key, out, ESCRITORIO_CACHE_TTL)
    return out


def _esc_tiempo_respuesta_y_pendientes(usuario, buzones_visibles, ventana_dias=90):
    """
    Calcula 2 métricas de valor real para el dueño del negocio:
      1. tiempo_respuesta_horas: promedio horas entre que llega un correo
         externo y la primera respuesta del mismo buzón al mismo remitente.
         Es proxy de "qué tan rápido atendemos a los clientes".
      2. sin_responder_7d: cantidad de correos recibidos hace >7 días que
         NUNCA se respondieron. Alerta de "esto se está acumulando".

    Implementación: cargar recibidos + enviados en RAM (sample N=2000),
    indexar enviados por (buzon, email destino), buscar primera respuesta
    para cada recibido. Cacheado 30min porque es O(N) sobre los últimos
    90 días — costoso para hacer en cada request.
    """
    import re
    cache_key = f'esc:tiempo_resp:{usuario.id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    ahora = timezone.now()
    desde = ahora - timedelta(days=ventana_dias)
    hace_7d = ahora - timedelta(days=7)
    visibles_ids = list(buzones_visibles.values_list('id', flat=True))
    if not visibles_ids:
        return {'tiempo_respuesta_horas': None, 'sin_responder_7d': 0}

    # Sample limitado para que la query no estalle (1500 inbox + 1500 sent)
    recibidos = list(
        Correo.objects
        .filter(buzon_id__in=visibles_ids, tipo_carpeta='inbox',
                fecha__gte=desde, fecha__lte=ahora)
        .order_by('fecha')
        .values('buzon_id', 'remitente', 'fecha')[:1500]
    )
    enviados = list(
        Correo.objects
        .filter(buzon_id__in=visibles_ids, tipo_carpeta='enviados',
                fecha__gte=desde, fecha__lte=ahora)
        .order_by('fecha')
        .values('buzon_id', 'destinatario', 'fecha')[:1500]
    )

    _email_re = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')

    def email_de(s: str) -> str | None:
        if not s:
            return None
        m = _email_re.search(s)
        return m.group(0).lower() if m else None

    # Index de enviados: (buzon_id, email_destino) → lista de fechas ordenadas
    sent_idx: dict = {}
    for e in enviados:
        em = email_de(e['destinatario'])
        if not em:
            continue
        sent_idx.setdefault((e['buzon_id'], em), []).append(e['fecha'])

    diffs_horas = []
    sin_responder = 0
    for r in recibidos:
        em_from = email_de(r['remitente'])
        if not em_from:
            continue
        candidates = sent_idx.get((r['buzon_id'], em_from), [])
        respuesta = next((d for d in candidates if d > r['fecha']), None)
        if respuesta:
            delta = respuesta - r['fecha']
            horas = delta.total_seconds() / 3600
            # Excluir outliers extremos (más de 30 días probablemente no
            # son respuestas a ESE correo sino correo nuevo).
            if 0 < horas < 24 * 30:
                diffs_horas.append(horas)
        else:
            # Sin respuesta detectada. Si es viejo, cuenta como pendiente.
            if r['fecha'] < hace_7d:
                sin_responder += 1

    if diffs_horas:
        promedio = sum(diffs_horas) / len(diffs_horas)
        # Decisión de unidad: si <24h mostrar horas, sino días
        if promedio < 24:
            tiempo_str = f'{promedio:.1f}h'
        else:
            tiempo_str = f'{promedio / 24:.1f}d'
    else:
        tiempo_str = '—'

    out = {
        'tiempo_respuesta_horas': round(sum(diffs_horas) / len(diffs_horas), 1)
                                  if diffs_horas else None,
        'tiempo_respuesta_str':   tiempo_str,
        'sin_responder_7d':       sin_responder,
        'sample_n':               len(recibidos),
        'respondidos_n':          len(diffs_horas),
    }
    cache.set(cache_key, out, ESCRITORIO_CACHE_TTL)
    return out


def _esc_top_perfiles_stacked(usuario, buzones_visibles, top=5):
    """
    Top N buzones por volumen, con breakdown de recibidos/enviados/otros
    para mostrar como barra apilada. Reemplaza a `_esc_top_perfiles` con
    info más rica.
    """
    cache_key = f'esc:perf_stacked:{usuario.id}:{top}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    visibles_ids = list(buzones_visibles.values_list('id', flat=True))
    # Total por buzón
    totales = dict(
        Correo.objects.filter(buzon_id__in=visibles_ids)
        .values('buzon_id').annotate(c=Count('id'))
        .values_list('buzon_id', 'c')
    )
    # Recibidos por buzón
    recibidos = dict(
        Correo.objects.filter(buzon_id__in=visibles_ids, tipo_carpeta='inbox')
        .values('buzon_id').annotate(c=Count('id'))
        .values_list('buzon_id', 'c')
    )
    # Enviados por buzón
    enviados = dict(
        Correo.objects.filter(buzon_id__in=visibles_ids, tipo_carpeta='enviados')
        .values('buzon_id').annotate(c=Count('id'))
        .values_list('buzon_id', 'c')
    )

    # Top N buzones por total
    buzones_data = [
        (bid, t) for bid, t in totales.items() if t > 0
    ]
    buzones_data.sort(key=lambda x: -x[1])
    buzones_data = buzones_data[:top]

    if not buzones_data:
        cache.set(cache_key, [], ESCRITORIO_CACHE_TTL)
        return []

    max_total = buzones_data[0][1] or 1
    # Map de buzones para obtener email/nombre
    buzones_map = {b.id: b for b in buzones_visibles}

    out = []
    for bid, total in buzones_data:
        b = buzones_map.get(bid)
        if not b:
            continue
        r = recibidos.get(bid, 0)
        e = enviados.get(bid, 0)
        o = max(0, total - r - e)
        out.append({
            'id':      bid,
            'email':   b.email,
            'nombre':  b.nombre or b.email,
            'iniciales': (b.email[:2] or '??').upper(),
            'total':   total,
            'recibidos': r,
            'enviados': e,
            'otros':   o,
            'pct':     round(total * 100 / max_total, 1),
            'pct_r':   round(r * 100 / total, 1) if total else 0,
            'pct_e':   round(e * 100 / total, 1) if total else 0,
            'pct_o':   round(o * 100 / total, 1) if total else 0,
        })

    cache.set(cache_key, out, ESCRITORIO_CACHE_TTL)
    return out


@portal_login_required
@throttle_user('escritorio', per_minute=60)
@never_cache
def escritorio_view(request):
    """
    Home del portal — escritorio tipo Windows con dashboard + widgets.
    Renderiza después del login en lugar del inbox directo.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    visibles_qs = usuario.buzones_visibles()
    if not visibles_qs.exists():
        request.session.flush()
        messages.error(request, 'No tienes buzones asignados. Contacta al administrador.')
        return redirect('login')

    ctx = {
        'usuario':           usuario,
        'stats':             _esc_stats_buzones(usuario, visibles_qs),
        'chart':             _esc_chart_ingresos(usuario, visibles_qs),
        'top_perfiles':      _esc_top_perfiles_stacked(usuario, visibles_qs),
        'top_temas':         _esc_top_temas(visibles_qs),
        'ultimos_correos':   _esc_ultimos_correos(usuario, visibles_qs),
        'archivos_recientes': _esc_archivos_recientes(visibles_qs),
        # ─── Dashboard expandido (Fase 1.5) ──────────────────────────────
        'kpis':              _esc_kpis_ejecutivos(usuario, visibles_qs),
        'tiempo_resp':       _esc_tiempo_respuesta_y_pendientes(usuario, visibles_qs),
        'volumen_mensual':   _esc_volumen_mensual(usuario, visibles_qs),
        'pie_carpetas':      _esc_pie_carpetas(usuario, visibles_qs),
        'top_remitentes':    _esc_top_remitentes_externos(usuario, visibles_qs),
        'heatmap':           _esc_heatmap_actividad(usuario, visibles_qs),
        'hoy': timezone.localdate(),
    }
    return render(request, 'correos/escritorio.html', ctx)


# ═════════════════════════════════════════════════════════════════════════════
# Apps Archivos / Contratos / Papelera (Fase 2 del rediseño)
# ═════════════════════════════════════════════════════════════════════════════

ARCHIVO_MAX_BYTES = 50 * 1024 * 1024   # 50 MB por archivo


def _archivos_visibles_qs(usuario):
    """
    Queryset base de archivos que ESTE usuario puede ver:
      - admin → todos
      - resto → propios + públicos + por perfil + compartidos explícitamente

    Devuelve queryset YA filtrado. Se compone con .filter() adicional.
    """
    if usuario.es_admin:
        return Archivo.objects.all()

    visibles_ids = list(usuario.buzones_visibles().values_list('id', flat=True))
    return Archivo.objects.filter(
        Q(creado_por=usuario)
        | Q(visibilidad=Archivo.Visibilidad.PUBLICO)
        | (Q(visibilidad=Archivo.Visibilidad.PERFIL) & Q(perfil_id__in=visibles_ids))
        | Q(comparticiones__usuario=usuario)
    ).distinct()


def _archivo_puede_ver(usuario, archivo) -> bool:
    """Delegamos al método del modelo (mantiene compatibilidad con callers viejos)."""
    return archivo.puede_ver(usuario)


@portal_login_required
@throttle_user('archivos', per_minute=60)
@never_cache
def archivos_list_view(request):
    """
    App Archivos: lista de archivos NO eliminados, NO contratos.
    Filtros: ?perfil=N, ?tema=texto, ?tipo=…, ?visibilidad=…, ?q=búsqueda.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    visibles_qs = usuario.buzones_visibles()

    qs = (_archivos_visibles_qs(usuario)
          .filter(eliminado_en__isnull=True)
          .exclude(tipo=Archivo.Tipo.CONTRATO)
          .select_related('perfil', 'creado_por')
          .prefetch_related('comparticiones__usuario')
          .order_by('-creado'))

    filtro_perfil = (request.GET.get('perfil') or '').strip()
    if filtro_perfil.isdigit():
        qs = qs.filter(perfil_id=int(filtro_perfil))
    filtro_tema = (request.GET.get('tema') or '').strip()
    if filtro_tema:
        # Match exact "Facturación" Y todas sus subcarpetas "Facturación/..."
        qs = qs.filter(Q(tema__iexact=filtro_tema) |
                       Q(tema__istartswith=filtro_tema + '/'))
    filtro_tipo = (request.GET.get('tipo') or '').strip()
    if filtro_tipo and filtro_tipo in {t.value for t in Archivo.Tipo}:
        qs = qs.filter(tipo=filtro_tipo)
    filtro_visib = (request.GET.get('visibilidad') or '').strip()
    if filtro_visib in {v.value for v in Archivo.Visibilidad}:
        qs = qs.filter(visibilidad=filtro_visib)
    busqueda = (request.GET.get('q') or '').strip()
    if busqueda:
        qs = qs.filter(Q(nombre__icontains=busqueda) |
                       Q(descripcion__icontains=busqueda) |
                       Q(tema__icontains=busqueda))

    # ─── Árbol de carpetas virtuales (vía tema con '/') ────────────────
    # Agrupamos por primer segmento del tema. Solo construimos el árbol
    # sobre el queryset ya filtrado por permisos (no leakea privados).
    carpetas_count: dict = {}
    for t in (_archivos_visibles_qs(usuario)
              .filter(eliminado_en__isnull=True)
              .exclude(tipo=Archivo.Tipo.CONTRATO)
              .exclude(tema='')
              .values_list('tema', flat=True)):
        # Cada nivel suma 1: "A/B/C" → cuenta para A, A/B, A/B/C
        partes = [p.strip() for p in t.split('/') if p.strip()]
        for i in range(len(partes)):
            path = '/'.join(partes[:i + 1])
            carpetas_count[path] = carpetas_count.get(path, 0) + 1

    # Lista ordenada por path para display jerárquico
    carpetas = sorted([
        {'path': p, 'nombre': p.rsplit('/', 1)[-1],
         'depth': p.count('/'), 'count': c}
        for p, c in carpetas_count.items()
    ], key=lambda x: x['path'].lower())

    total = qs.count()
    paginator = Paginator(qs, 50)
    page_num = request.GET.get('p') or 1
    page = paginator.get_page(page_num)

    return render(request, 'correos/archivos_list.html', {
        'archivos':       page.object_list,
        'page':           page,
        'paginator':      paginator,
        'total':          total,
        'carpetas':       carpetas,
        'buzones_visibles': visibles_qs,
        'filtro_perfil':  filtro_perfil,
        'filtro_tema':    filtro_tema,
        'filtro_tipo':    filtro_tipo,
        'filtro_visib':   filtro_visib,
        'busqueda':       busqueda,
        'tipos_choices':  Archivo.Tipo.choices,
        'visibilidades':  Archivo.Visibilidad.choices,
        'app_label':      'Archivos',
        'app_color':      '#2563eb',
    })


@portal_login_required
@throttle_user('archivos_upload', per_minute=20)
@require_POST
def archivos_upload_view(request):
    """Sube un archivo nuevo. Form POST simple, sin Django Forms."""
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    f = request.FILES.get('archivo')
    if not f:
        messages.error(request, 'Seleccioná un archivo.')
        return redirect(request.POST.get('next') or 'archivos')

    if f.size > ARCHIVO_MAX_BYTES:
        messages.error(request,
                       f'Archivo demasiado grande. Máximo {ARCHIVO_MAX_BYTES // 1024 // 1024} MB.')
        return redirect(request.POST.get('next') or 'archivos')

    nombre = (request.POST.get('nombre') or f.name).strip()[:200] or f.name[:200]
    tipo_raw = (request.POST.get('tipo') or Archivo.Tipo.DOCUMENTO).strip()
    tipo = tipo_raw if tipo_raw in {t.value for t in Archivo.Tipo} else Archivo.Tipo.DOCUMENTO

    perfil_id = (request.POST.get('perfil') or '').strip()
    perfil = None
    if perfil_id.isdigit():
        # Auth: solo permitir asignar a un buzón visible para el usuario
        try:
            perfil = usuario.buzones_visibles().get(id=int(perfil_id))
        except Buzon.DoesNotExist:
            perfil = None

    # Visibilidad: privado/perfil/publico (default: si tiene perfil → perfil,
    # sino → privado).
    visib_raw = (request.POST.get('visibilidad') or '').strip()
    if visib_raw in {v.value for v in Archivo.Visibilidad}:
        visibilidad = visib_raw
    else:
        visibilidad = (Archivo.Visibilidad.PERFIL if perfil
                       else Archivo.Visibilidad.PRIVADO)

    # Coherencia: si pidió PERFIL pero no asignó perfil → cae a PRIVADO
    if visibilidad == Archivo.Visibilidad.PERFIL and not perfil:
        visibilidad = Archivo.Visibilidad.PRIVADO

    archivo = Archivo(
        nombre=nombre,
        archivo=f,
        mime_type=(f.content_type or '')[:200],
        tamano_bytes=f.size,
        tipo=tipo,
        perfil=perfil,
        tema=(request.POST.get('tema') or '').strip()[:80],
        visibilidad=visibilidad,
        descripcion=(request.POST.get('descripcion') or '').strip(),
        creado_por=usuario,
    )

    # Fecha del documento (no de upload)
    fecha_str = (request.POST.get('fecha') or '').strip()
    if fecha_str:
        try:
            from datetime import datetime as _dt
            archivo.fecha = _dt.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    # Campos contrato-only
    if tipo == Archivo.Tipo.CONTRATO:
        archivo.contrato_partes = (request.POST.get('partes') or '').strip()[:300]
        venc_str = (request.POST.get('vencimiento') or '').strip()
        if venc_str:
            try:
                from datetime import datetime as _dt
                archivo.contrato_vencimiento = _dt.strptime(venc_str, '%Y-%m-%d').date()
            except ValueError:
                pass

    archivo.save()

    try:
        EventoAuditoria.objects.create(
            usuario=usuario, accion='archivo_subir',
            target_tipo='archivo', target_id=archivo.id,
            meta={'nombre': nombre, 'tipo': tipo, 'size': f.size},
            ip_hash=hash_ip(_get_ip(request)),
        )
    except Exception:
        logger.exception('audit archivo_subir falló')

    messages.success(request, f'Subido: {nombre}')
    # Redirige a la app correcta según tipo
    if tipo == Archivo.Tipo.CONTRATO:
        return redirect('contratos')
    return redirect('archivos')


@portal_login_required
@throttle_user('archivo_descargar', per_minute=120)
def archivo_descargar_view(request, archivo_id):
    """
    Sirve el archivo al usuario. Auth check por visibilidad.
    Query params:
      - ?inline=1 → fuerza Content-Disposition: inline (preview en viewer)
        Solo para tipos seguros (PDF, imagen, audio/video). Para el resto
        se ignora y descarga normal.
      - default → as_attachment según `Archivo.tamano_bytes` y mime.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    archivo = get_object_or_404(Archivo, id=archivo_id, eliminado_en__isnull=True)
    if not _archivo_puede_ver(usuario, archivo):
        raise Http404

    try:
        f = archivo.archivo.open('rb')
    except FileNotFoundError:
        raise Http404('Archivo no encontrado en disco')

    mime = (archivo.mime_type or '').lower()
    tipos_inline = (
        mime.startswith('image/')
        or mime == 'application/pdf'
        or mime.startswith('audio/')
        or mime.startswith('video/')
        or mime.startswith('text/')
    )
    quiere_inline = request.GET.get('inline') == '1'
    inline = quiere_inline and tipos_inline

    response = FileResponse(
        f,
        content_type=archivo.mime_type or 'application/octet-stream',
        as_attachment=not inline,
        filename=archivo.nombre,
    )
    response['X-Content-Type-Options'] = 'nosniff'
    if inline:
        # CSP estricto al servir inline — anti XSS en el archivo
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'none'; "
            "object-src 'self'; frame-ancestors 'self'"
        )
    return response


@portal_login_required
@require_POST
def archivo_borrar_view(request, archivo_id):
    """Soft-delete: mueve a papelera. NO borra de disco."""
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    archivo = get_object_or_404(Archivo, id=archivo_id, eliminado_en__isnull=True)
    if not _archivo_puede_ver(usuario, archivo):
        raise Http404

    nombre = archivo.nombre
    tipo = archivo.tipo
    archivo.soft_delete(usuario)
    try:
        EventoAuditoria.objects.create(
            usuario=usuario, accion='archivo_eliminar',
            target_tipo='archivo', target_id=archivo_id,
            meta={'nombre': nombre, 'tipo': tipo},
            ip_hash=hash_ip(_get_ip(request)),
        )
    except Exception:
        logger.exception('audit archivo_eliminar falló')

    messages.success(request, f'Movido a papelera: {nombre}')
    if tipo == Archivo.Tipo.CONTRATO:
        return redirect('contratos')
    return redirect('archivos')


@portal_login_required
@throttle_user('contratos', per_minute=60)
@never_cache
def contratos_list_view(request):
    """App Contratos: archivos con tipo=contrato y NO eliminados."""
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    visibles_qs = usuario.buzones_visibles()

    qs = (_archivos_visibles_qs(usuario)
          .filter(eliminado_en__isnull=True, tipo=Archivo.Tipo.CONTRATO)
          .select_related('perfil', 'creado_por')
          .prefetch_related('comparticiones__usuario')
          .order_by('-creado'))

    filtro_perfil = (request.GET.get('perfil') or '').strip()
    if filtro_perfil.isdigit():
        qs = qs.filter(perfil_id=int(filtro_perfil))
    busqueda = (request.GET.get('q') or '').strip()
    if busqueda:
        qs = qs.filter(Q(nombre__icontains=busqueda) |
                       Q(descripcion__icontains=busqueda) |
                       Q(contrato_partes__icontains=busqueda))

    # Próximos a vencer (siguientes 30 días)
    en_30d = timezone.localdate() + timedelta(days=30)
    prox_vencer = qs.filter(
        contrato_vencimiento__isnull=False,
        contrato_vencimiento__lte=en_30d,
        contrato_vencimiento__gte=timezone.localdate(),
    ).order_by('contrato_vencimiento')

    total = qs.count()
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('p') or 1)

    return render(request, 'correos/archivos_list.html', {
        'archivos':       page.object_list,
        'page':           page,
        'paginator':      paginator,
        'total':          total,
        'prox_vencer':    prox_vencer,
        'buzones_visibles': visibles_qs,
        'filtro_perfil':  filtro_perfil,
        'busqueda':       busqueda,
        'tipos_choices':  [(Archivo.Tipo.CONTRATO, 'Contrato')],
        'forzar_tipo':    Archivo.Tipo.CONTRATO,
        'visibilidades':  Archivo.Visibilidad.choices,
        'app_label':      'Contratos',
        'app_color':      '#d97706',
        'is_contratos':   True,
    })


@portal_login_required
@throttle_user('papelera', per_minute=60)
@never_cache
def papelera_list_view(request):
    """App Papelera: archivos eliminados de TODAS las apps."""
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    qs = (_archivos_visibles_qs(usuario)
          .filter(eliminado_en__isnull=False)
          .select_related('perfil', 'creado_por', 'eliminado_por')
          .order_by('-eliminado_en'))

    total = qs.count()
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('p') or 1)

    return render(request, 'correos/papelera_list.html', {
        'archivos':  page.object_list,
        'page':      page,
        'paginator': paginator,
        'total':     total,
        'app_label': 'Papelera',
        'app_color': 'var(--text-muted)',
    })


@portal_login_required
@require_POST
def archivo_restaurar_view(request, archivo_id):
    """Sacar de papelera (vuelve a su app de origen según tipo)."""
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    archivo = get_object_or_404(Archivo, id=archivo_id, eliminado_en__isnull=False)
    if not _archivo_puede_ver(usuario, archivo):
        raise Http404

    archivo.restaurar()
    try:
        EventoAuditoria.objects.create(
            usuario=usuario, accion='archivo_restaurar',
            target_tipo='archivo', target_id=archivo_id,
            meta={'nombre': archivo.nombre, 'tipo': archivo.tipo},
            ip_hash=hash_ip(_get_ip(request)),
        )
    except Exception:
        logger.exception('audit archivo_restaurar falló')

    messages.success(request, f'Restaurado: {archivo.nombre}')
    return redirect('papelera')


@portal_login_required
@require_POST
def archivo_borrar_permanente_view(request, archivo_id):
    """Borrado físico del archivo. SOLO admins (irreversible)."""
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')
    if not usuario.es_admin:
        messages.error(request, 'Solo administradores pueden borrar permanente.')
        return redirect('papelera')

    archivo = get_object_or_404(Archivo, id=archivo_id, eliminado_en__isnull=False)
    nombre = archivo.nombre
    archivo_id_log = archivo.id
    if archivo.archivo:
        try:
            archivo.archivo.delete(save=False)
        except Exception:
            logger.warning('No se pudo borrar el archivo físico de %s', nombre)
    archivo.delete()
    try:
        EventoAuditoria.objects.create(
            usuario=usuario, accion='archivo_borrar_perm',
            target_tipo='archivo', target_id=archivo_id_log,
            meta={'nombre': nombre},
            ip_hash=hash_ip(_get_ip(request)),
        )
    except Exception:
        logger.exception('audit archivo_borrar_perm falló')

    messages.success(request, f'Borrado permanente: {nombre}')
    return redirect('papelera')


# ─── Versiones de un archivo ──────────────────────────────────────────────
@portal_login_required
@throttle_user('archivos_upload', per_minute=20)
@require_POST
def archivo_subir_version_view(request, archivo_id):
    """
    Sube una nueva versión de un archivo existente. La nueva versión es un
    Archivo nuevo con `version_padre = raiz` y `version_num = max + 1`.
    Hereda tipo/perfil/visibilidad del padre (NO se pueden cambiar acá).
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    base = get_object_or_404(Archivo, id=archivo_id, eliminado_en__isnull=True)
    if not _archivo_puede_ver(usuario, base):
        raise Http404
    # Solo el uploader original o admins pueden versionar
    if not usuario.es_admin and base.creado_por_id != usuario.id:
        messages.error(request, 'Solo el propietario o un admin puede versionar.')
        return redirect('archivos' if base.tipo != Archivo.Tipo.CONTRATO else 'contratos')

    f = request.FILES.get('archivo')
    if not f:
        messages.error(request, 'Seleccioná un archivo para la nueva versión.')
        return redirect('archivos' if base.tipo != Archivo.Tipo.CONTRATO else 'contratos')

    if f.size > ARCHIVO_MAX_BYTES:
        messages.error(request, f'Archivo demasiado grande. Máximo {ARCHIVO_MAX_BYTES // 1024 // 1024} MB.')
        return redirect('archivos' if base.tipo != Archivo.Tipo.CONTRATO else 'contratos')

    from django.db.models import Max as _DbMax
    raiz_id = base.version_padre_id or base.id
    ultimo_num = (Archivo.objects
                  .filter(Q(id=raiz_id) | Q(version_padre_id=raiz_id))
                  .aggregate(maxv=_DbMax('version_num'))['maxv'] or 1)

    nueva = Archivo(
        nombre=base.nombre,
        archivo=f,
        mime_type=(f.content_type or '')[:200],
        tamano_bytes=f.size,
        tipo=base.tipo,
        perfil=base.perfil,
        tema=base.tema,
        visibilidad=base.visibilidad,
        descripcion=base.descripcion,
        creado_por=usuario,
        version_padre_id=raiz_id,
        version_num=ultimo_num + 1,
        version_nota=(request.POST.get('nota') or '').strip()[:300],
    )
    nueva.save()

    try:
        EventoAuditoria.objects.create(
            usuario=usuario, accion='archivo_versionar',
            target_tipo='archivo', target_id=nueva.id,
            meta={'raiz': raiz_id, 'version': nueva.version_num, 'nombre': base.nombre},
            ip_hash=hash_ip(_get_ip(request)),
        )
    except Exception:
        logger.exception('audit archivo_versionar falló')

    messages.success(request, f'Versión {nueva.version_num} de «{base.nombre}» subida.')
    if base.tipo == Archivo.Tipo.CONTRATO:
        return redirect('contratos')
    return redirect('archivos')


# ─── Compartir archivo con un usuario específico ──────────────────────────
@portal_login_required
@require_POST
def archivo_compartir_view(request, archivo_id):
    """
    Comparte un archivo con un UsuarioPortal por email.
    Solo el uploader o admin puede compartir.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    archivo = get_object_or_404(Archivo, id=archivo_id, eliminado_en__isnull=True)
    if not usuario.es_admin and archivo.creado_por_id != usuario.id:
        messages.error(request, 'Solo el propietario o un admin puede compartir.')
        return redirect('archivos')

    email_raw = (request.POST.get('email') or '').strip().lower()[:200]
    if not email_raw:
        messages.error(request, 'Indicá un email del portal.')
        return redirect('archivos')

    try:
        destinatario = UsuarioPortal.objects.get(email__iexact=email_raw)
    except UsuarioPortal.DoesNotExist:
        messages.error(request, f'No hay usuario del portal con email «{email_raw}».')
        return redirect('archivos')

    if destinatario.id == usuario.id:
        messages.info(request, 'No tiene sentido compartir contigo mismo.')
        return redirect('archivos')

    _, creado = ArchivoComparticion.objects.get_or_create(
        archivo=archivo, usuario=destinatario,
        defaults={'compartido_por': usuario},
    )
    if creado:
        try:
            EventoAuditoria.objects.create(
                usuario=usuario, accion='archivo_compartir',
                target_tipo='archivo', target_id=archivo.id,
                meta={'con_usuario': destinatario.email, 'nombre': archivo.nombre},
                ip_hash=hash_ip(_get_ip(request)),
            )
        except Exception:
            logger.exception('audit archivo_compartir falló')
        messages.success(request, f'Compartido «{archivo.nombre}» con {destinatario.email}.')
    else:
        messages.info(request, f'Ya estaba compartido con {destinatario.email}.')

    if archivo.tipo == Archivo.Tipo.CONTRATO:
        return redirect('contratos')
    return redirect('archivos')


@portal_login_required
@require_POST
def archivo_descompartir_view(request, archivo_id, comparticion_id):
    """Quita una compartición. Solo uploader o admin."""
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    archivo = get_object_or_404(Archivo, id=archivo_id)
    if not usuario.es_admin and archivo.creado_por_id != usuario.id:
        raise Http404

    comp = get_object_or_404(ArchivoComparticion, id=comparticion_id, archivo=archivo)
    email_log = comp.usuario.email if comp.usuario else '(sin user)'
    comp.delete()

    try:
        EventoAuditoria.objects.create(
            usuario=usuario, accion='archivo_descompartir',
            target_tipo='archivo', target_id=archivo.id,
            meta={'con_usuario': email_log, 'nombre': archivo.nombre},
            ip_hash=hash_ip(_get_ip(request)),
        )
    except Exception:
        logger.exception('audit archivo_descompartir falló')

    messages.success(request, f'Quitada compartición con {email_log}.')
    if archivo.tipo == Archivo.Tipo.CONTRATO:
        return redirect('contratos')
    return redirect('archivos')


# ─── Vincular archivo a un correo existente ───────────────────────────────
@portal_login_required
@require_POST
def correo_vincular_archivo_view(request, correo_id):
    """
    Asocia un Archivo a un Correo (no es adjunto SMTP — solo metadata).
    El user debe poder ver AMBOS para crear el vínculo.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404

    arc_id = (request.POST.get('archivo_id') or '').strip()
    if not arc_id.isdigit():
        messages.error(request, 'Falta indicar el archivo.')
        return redirect('detalle', correo_id=correo_id)

    archivo = get_object_or_404(Archivo, id=int(arc_id), eliminado_en__isnull=True)
    if not _archivo_puede_ver(usuario, archivo):
        raise Http404

    _, creado = ArchivoVinculo.objects.get_or_create(
        archivo=archivo, correo=correo,
        defaults={'vinculado_por': usuario},
    )
    if creado:
        try:
            EventoAuditoria.objects.create(
                usuario=usuario, accion='archivo_vincular',
                target_tipo='correo', target_id=correo.id,
                meta={'archivo_id': archivo.id, 'archivo_nombre': archivo.nombre},
                ip_hash=hash_ip(_get_ip(request)),
            )
        except Exception:
            logger.exception('audit archivo_vincular falló')
        messages.success(request, f'Archivo «{archivo.nombre}» vinculado al correo.')
    else:
        messages.info(request, 'El archivo ya estaba vinculado.')

    return redirect('detalle', correo_id=correo_id)


@portal_login_required
@require_POST
def correo_desvincular_archivo_view(request, correo_id, vinculo_id):
    """Quita un vínculo archivo↔correo. El user debe ver el correo."""
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    correo = get_object_or_404(Correo, id=correo_id)
    if not usuario.puede_ver(correo.buzon):
        raise Http404

    vinc = get_object_or_404(ArchivoVinculo, id=vinculo_id, correo=correo)
    arc_nombre = vinc.archivo.nombre if vinc.archivo else '(borrado)'
    vinc.delete()

    try:
        EventoAuditoria.objects.create(
            usuario=usuario, accion='archivo_desvincular',
            target_tipo='correo', target_id=correo.id,
            meta={'archivo_nombre': arc_nombre},
            ip_hash=hash_ip(_get_ip(request)),
        )
    except Exception:
        logger.exception('audit archivo_desvincular falló')

    messages.success(request, f'Vínculo con «{arc_nombre}» quitado.')
    return redirect('detalle', correo_id=correo_id)
