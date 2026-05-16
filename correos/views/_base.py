import logging
import re
import time
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncDate, TruncMonth
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from taller.anti_bot import verify_turnstile

from .. import captcha
from .. import totp as totp_helpers
from ..models import (
    Adjunto,
    Archivo,
    ArchivoComparticion,
    ArchivoVinculo,
    BorradorAdjunto,
    BorradorCorreo,
    Buzon,
    CategoriaTema,
    Correo,
    CorreoEliminado,
    CorreoEnviado,
    CorreoLeido,
    CorreoSnooze,
    Etiqueta,
    EventoAuditoria,
    IntentoLogin,
    ReenvioCorreo,
    UsuarioPortal,
    hash_ip,
)
from ..templatetags.correos_tags import html_a_texto
from ..threading import (
    create_thread_for as thread_create_for,
    recompute_thread_cache as thread_recompute,
)
from ..throttle import throttle_user

logger = logging.getLogger('correos.views')


# Tiempo máximo entre password OK y completar 2FA (segundos).
PRE_2FA_TTL = 5 * 60

# "Recordarme" extiende la cookie a 30 días con sliding (SESSION_SAVE_EVERY_REQUEST).
# Re-pedimos 2FA cada RE_2FA_AFTER_DAYS para frenar cookies robadas en sesiones largas.
REMEMBER_ME_AGE_DAYS = 30
RE_2FA_AFTER_DAYS    = 30

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
            subject=f'[RSP · Seguridad] {asunto}',
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
        # Resolver de URL: necesario para chequear si la URL actual está en
        # whitelist de excepciones (no redirect a setup/verify desde ahí).
        from django.urls import Resolver404, resolve
        try:
            match = resolve(request.path_info)
            url_name = match.url_name
        except Resolver404:
            url_name = ''
        whitelist = {'setup_2fa', 'verify_2fa', 'logout', 'descargar_recovery_pdf'}

        if getattr(settings, 'PORTAL_REQUIRE_2FA', True):
            usuario = _usuario_actual(request)
            # (a) Usuario nunca configuró TOTP → mandamos al setup.
            if usuario and not usuario.totp_activo:
                if url_name not in whitelist:
                    return redirect('setup_2fa')
            # (b) Sesión activa pero pasaron RE_2FA_AFTER_DAYS desde el último
            #     2FA → re-pedimos verify para frenar cookies robadas en sesiones
            #     largas (modo "recordarme"). Sin esta defensa, una cookie
            #     persistente de 30 días le da al atacante 30 días enteros.
            elif usuario and usuario.totp_activo:
                ultima = request.session.get('ultima_2fa_at', 0)
                if ultima and url_name not in whitelist:
                    edad = int(time.time()) - int(ultima)
                    if edad > RE_2FA_AFTER_DAYS * 24 * 60 * 60:
                        request.session['re_2fa_user_id'] = usuario.id
                        request.session['re_2fa_at']      = int(time.time())
                        return redirect('verify_2fa')
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


