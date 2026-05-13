"""
Vistas públicas (sin auth) del módulo de agendamiento.

Flujo:
  1. /agendar/                       → catálogo + form + calendario
  2. POST /agendar/confirmar/        → valida + anti-bot → crea Reserva
                                       (estado=pendiente_email) → manda código
                                       de 6 dígitos → redirect a /verificar/
  3. /agendar/verificar/?t=<token>   → input del código → al validar pasa a
                                       confirmada_email → manda confirmación
                                       cliente + admin → redirect a /r/<token>/
  4. /agendar/r/<token>/             → ver detalles de mi reserva
  5. POST /agendar/r/<token>/cancelar/ → cliente cancela su propia reserva
  6. /agendar/disponibilidad/?fecha=YYYY-MM-DD → AJAX JSON con slots del día

Anti-bot: cada paso hace pasar capas (Turnstile + captcha Fernet +
honeypot + rate-limit por IP-hash + email verify + blocklist). Cada fallo
loguea ReservaIntento con motivo para forensia.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date as date_cls, datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from correos import captcha as fernet_captcha
from correos.models import hash_ip

from . import anti_bot
from .models import (
    BloqueoCalendario,
    ItemCatalogo,
    Reserva,
    ReservaIntento,
    generar_token_publico,
    hash_token,
    patente_validator,
    telefono_validator,
)
from .utils import (
    es_dia_laboral,
    fechas_disponibles_proximas,
    slots_de_la_fecha,
)

logger = logging.getLogger('taller.views')


# ─── Helpers ───────────────────────────────────────────────────────────────
def _get_ip(request) -> str:
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _ua(request) -> str:
    return (request.META.get('HTTP_USER_AGENT') or '')[:500]


def _log_intento(request, ip_h: str, email: str, motivo: str, exito: bool = False):
    try:
        ReservaIntento.objects.create(
            ip_hash=ip_h,
            user_agent=_ua(request),
            email_intentado=(email or '')[:254],
            motivo=motivo,
            exito=exito,
        )
    except Exception:
        # Nunca tirar la vista por un fallo de logging
        pass


def _items_activos_por_tipo() -> dict:
    """Devuelve dict {'servicio': [...], 'repuesto': [...]} con items activos ordenados."""
    qs = ItemCatalogo.objects.filter(activo=True).order_by('orden', 'nombre')
    out = {'servicio': [], 'repuesto': []}
    for it in qs:
        out[it.tipo].append(it)
    return out


# ─── 1) Página principal: catálogo + form + calendario ────────────────────
@never_cache
@require_GET
def agendar_view(request):
    items_por_tipo = _items_activos_por_tipo()
    fechas_calendario = fechas_disponibles_proximas(dias=28)

    return render(request, 'taller/agendar.html', {
        'servicios':       items_por_tipo['servicio'],
        'repuestos':       items_por_tipo['repuesto'],
        'fechas_calendario': fechas_calendario,
        'turnstile_site_key': getattr(settings, 'TURNSTILE_SITE_KEY', ''),
        'fernet_challenge': fernet_captcha.generar_challenge(),
        # Categorías para chips
        'categorias_servicios': [
            (c.value, c.label) for c in ItemCatalogo.Categoria
            if c.value not in {} and not c.value.startswith('rep_') and c.value != 'otros'
        ],
        'categorias_repuestos': [
            (c.value, c.label) for c in ItemCatalogo.Categoria
            if c.value.startswith('rep_')
        ],
    })


# ─── 2) Disponibilidad de una fecha (AJAX) ────────────────────────────────
@never_cache
@require_GET
def disponibilidad_view(request):
    fecha_str = request.GET.get('fecha', '')
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return HttpResponseBadRequest('fecha inválida (esperado YYYY-MM-DD)')

    laboral, motivo = es_dia_laboral(fecha)
    if not laboral:
        return JsonResponse({'fecha': fecha_str, 'laboral': False, 'motivo': motivo, 'slots': []})

    slots = slots_de_la_fecha(fecha)
    return JsonResponse({
        'fecha':   fecha_str,
        'laboral': True,
        'motivo':  '',
        'slots':   slots,
    })


# ─── 3) POST /confirmar/: crea reserva pendiente_email ────────────────────
@never_cache
@require_POST
def confirmar_reserva_view(request):
    """
    Recibe el form completo, valida en capas, crea Reserva pendiente_email,
    manda código de 6 dígitos al email del cliente y redirige a /verificar/.
    """
    from archivo_pietramonte.email_utils import safe_send

    ip_h = hash_ip(_get_ip(request))

    # ─── Capa 1: rate-limit de intentos ────────────────────────────────
    anti_bot.rl_intento(ip_h)
    if anti_bot.rl_bloqueado_intentos(ip_h):
        _log_intento(request, ip_h, '', 'throttled')
        messages.error(request, 'Demasiados intentos desde tu conexión. Esperá unos minutos.')
        return redirect('agendar')

    # ─── Capa 2: rate-limit de reservas exitosas (anti-flood de reservas) ─
    if anti_bot.rl_bloqueado_reservas(ip_h):
        _log_intento(request, ip_h, '', 'throttled')
        messages.error(request, 'Llegaste al límite de reservas por hora. Esperá un poco antes de agendar otra.')
        return redirect('agendar')

    # ─── Capa 3: honeypot ──────────────────────────────────────────────
    if anti_bot.honeypot_lleno(request.POST):
        _log_intento(request, ip_h, '', 'honeypot')
        # Respuesta genérica — el bot no sabe que fue detectado
        messages.error(request, 'No pudimos procesar la reserva. Intentá de nuevo.')
        return redirect('agendar')

    # ─── Capa 4: Cloudflare Turnstile ──────────────────────────────────
    turnstile_token = request.POST.get('cf-turnstile-response') or ''
    if not anti_bot.verify_turnstile(turnstile_token, ip=_get_ip(request)):
        _log_intento(request, ip_h, '', 'turnstile_fail')
        messages.error(request, 'Verificación anti-bot falló. Recargá la página y volvé a intentar.')
        return redirect('agendar')

    # ─── Capa 5: captcha Fernet (visual) ───────────────────────────────
    captcha_token = request.POST.get('captcha_token') or ''
    captcha_sel   = request.POST.getlist('captcha_seleccion[]')
    try:
        fernet_captcha.verificar(captcha_token, captcha_sel)
    except fernet_captcha.CaptchaError:
        _log_intento(request, ip_h, '', 'captcha_fail')
        messages.error(request, 'Captcha incorrecto o expirado. Resolvelo de nuevo.')
        return redirect('agendar')

    # ─── Capa 6: validación de campos ──────────────────────────────────
    email     = (request.POST.get('cliente_email') or '').strip().lower()
    nombre    = (request.POST.get('cliente_nombre') or '').strip()[:120]
    telefono  = (request.POST.get('cliente_telefono') or '').strip()[:20]
    patente   = (request.POST.get('patente') or '').strip().upper().replace(' ', '')[:10]
    marca     = (request.POST.get('marca') or '').strip()[:40]
    modelo    = (request.POST.get('modelo') or '').strip()[:60]
    anio_raw  = (request.POST.get('anio') or '').strip()
    motor     = (request.POST.get('motor') or '').strip()[:40]
    km_raw    = (request.POST.get('kilometraje') or '').strip()
    contexto  = (request.POST.get('contexto_problema') or '').strip()[:2000]
    fecha_str = (request.POST.get('fecha') or '').strip()
    hora_str  = (request.POST.get('hora_inicio') or '').strip()
    item_ids  = request.POST.getlist('item_ids[]')

    errores = []
    # Email + blocklist
    try:
        validate_email(email)
    except ValidationError:
        errores.append('Email inválido.')
    if email and anti_bot.email_es_desechable(email):
        _log_intento(request, ip_h, email, 'email_desechable')
        errores.append('No aceptamos emails temporales/desechables. Usá uno permanente.')

    if not nombre:
        errores.append('Nombre requerido.')
    if not patente or not _valida_patente(patente):
        errores.append('Patente inválida (solo letras y números, 4-8 caracteres).')
    if not marca:
        errores.append('Marca del vehículo requerida.')
    if not modelo:
        errores.append('Modelo del vehículo requerido.')
    if not _valida_telefono(telefono):
        errores.append('Teléfono inválido. Formato: +56912345678 o similar.')

    anio = None
    if anio_raw:
        try:
            anio = int(anio_raw)
            if anio < 1950 or anio > timezone.localdate().year + 1:
                anio = None
        except ValueError:
            pass

    kilometraje = None
    if km_raw:
        try:
            kilometraje = max(0, int(km_raw))
        except ValueError:
            pass

    # Fecha + hora
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        hora  = datetime.strptime(hora_str, '%H:%M').time()
    except ValueError:
        _log_intento(request, ip_h, email, 'fuera_horario')
        errores.append('Fecha u hora inválidas.')
        fecha = None
        hora  = None

    if fecha and hora:
        laboral, motivo_no_lab = es_dia_laboral(fecha)
        if not laboral:
            _log_intento(request, ip_h, email, 'feriado')
            errores.append(f'Esa fecha no se puede agendar: {motivo_no_lab}.')

        # Verificar slot disponible (anti race condition simple)
        ocupado = Reserva.objects.filter(
            fecha=fecha, hora_inicio=hora,
            estado__in=[
                Reserva.Estado.PENDIENTE_EMAIL,
                Reserva.Estado.CONFIRMADA_EMAIL,
                Reserva.Estado.CONFIRMADA_LLAMADA,
            ],
        ).exists()
        if ocupado:
            _log_intento(request, ip_h, email, 'slot_ocupado')
            errores.append('Ese horario se acaba de tomar. Elegí otro.')

    # Items
    items_qs = ItemCatalogo.objects.filter(id__in=item_ids, activo=True) if item_ids else ItemCatalogo.objects.none()
    if not items_qs.exists():
        errores.append('Tenés que elegir al menos un servicio o repuesto.')

    if errores:
        for e in errores:
            messages.error(request, e)
        return redirect('agendar')

    # ─── Crear Reserva ─────────────────────────────────────────────────
    token_plano = generar_token_publico()
    duracion    = sum(it.duracion_min for it in items_qs)
    total       = sum(it.precio_referencia_clp for it in items_qs)

    reserva = Reserva.objects.create(
        token_hash=hash_token(token_plano),
        cliente_nombre=nombre,
        cliente_email=email,
        cliente_telefono=telefono,
        patente=patente,
        marca=marca, modelo=modelo, anio=anio, motor=motor, kilometraje=kilometraje,
        contexto_problema=contexto,
        fecha=fecha, hora_inicio=hora,
        duracion_estimada_min=duracion,
        total_referencial_clp=total,
        estado=Reserva.Estado.PENDIENTE_EMAIL,
        ip_hash_creacion=ip_h,
        user_agent_creacion=_ua(request),
    )
    reserva.items.set(items_qs)

    # ─── Mandar código de 6 dígitos al email del cliente ───────────────
    codigo = anti_bot.generar_codigo_email(email)
    safe_send(
        asunto=f'Código de verificación · {codigo}',
        para=email,
        template='taller/email/codigo_verificacion',
        contexto={'codigo': codigo, 'reserva': reserva},
        from_alias=getattr(settings, 'EMAIL_AGENDA_FROM', None),
        reply_to=[settings.EMAIL_REPLY_TO_AGENDA] if settings.EMAIL_REPLY_TO_AGENDA else None,
    )

    _log_intento(request, ip_h, email, 'email_no_verif', exito=False)

    # Guardamos el token en sesión para que el form de verificación lo recupere
    # (alternativa: pasarlo por GET, pero es más feo en URL)
    request.session['agendar_token'] = token_plano
    return redirect('verificar_email')


# ─── 4) Verificar código de email ─────────────────────────────────────────
@never_cache
@require_http_methods(['GET', 'POST'])
def verificar_email_view(request):
    """
    Pide el código de 6 dígitos que se mandó al email del cliente. Al validar,
    pasa la Reserva a `confirmada_email` y manda los emails de confirmación
    a cliente + admin (en Commit H — por ahora solo lo marca confirmada).
    """
    token = request.session.get('agendar_token')
    if not token:
        messages.error(request, 'Sesión expirada. Empezá de nuevo.')
        return redirect('agendar')

    try:
        reserva = Reserva.objects.get(token_hash=hash_token(token))
    except Reserva.DoesNotExist:
        messages.error(request, 'No encontramos tu reserva. Empezá de nuevo.')
        return redirect('agendar')

    if reserva.estado != Reserva.Estado.PENDIENTE_EMAIL:
        # Ya está confirmada (o cancelada) — saltamos al detalle
        return redirect('ver_reserva', token=token)

    if request.method == 'GET':
        return render(request, 'taller/verificar_email.html', {
            'reserva': reserva,
            'email_oculto': _ofuscar_email(reserva.cliente_email),
        })

    # POST
    codigo = (request.POST.get('codigo') or '').strip()
    ip_h   = hash_ip(_get_ip(request))

    if not anti_bot.verificar_codigo_email(reserva.cliente_email, codigo):
        _log_intento(request, ip_h, reserva.cliente_email, 'email_no_verif')
        messages.error(request, 'Código incorrecto o expirado. Pedí uno nuevo si hace falta.')
        return render(request, 'taller/verificar_email.html', {
            'reserva': reserva,
            'email_oculto': _ofuscar_email(reserva.cliente_email),
        }, status=400)

    # ─── Código OK → confirmar ─────────────────────────────────────────
    from archivo_pietramonte.email_utils import safe_send

    reserva.estado = Reserva.Estado.CONFIRMADA_EMAIL
    reserva.confirmada_email_en = timezone.now()
    reserva.save(update_fields=['estado', 'confirmada_email_en'])
    _log_intento(request, ip_h, reserva.cliente_email, 'exito', exito=True)
    anti_bot.rl_reserva(ip_h)

    # ─── Email de confirmación al cliente (con link al detalle) ────────
    safe_send(
        asunto=f'Reserva confirmada: {reserva.fecha:%d/%m/%Y} a las {reserva.hora_inicio:%H:%M}',
        para=reserva.cliente_email,
        template='taller/email/nueva_reserva_cliente',
        contexto={
            'reserva': reserva,
            'token': token,
            'site_url': request.build_absolute_uri('/').rstrip('/'),
        },
        from_alias=getattr(settings, 'EMAIL_AGENDA_FROM', None),
        reply_to=[settings.EMAIL_REPLY_TO_AGENDA] if settings.EMAIL_REPLY_TO_AGENDA else None,
    )

    # ─── Email de notificación a los admins ────────────────────────────
    admin_emails = getattr(settings, 'ADMIN_NOTIFY_AGENDA', [])
    if admin_emails:
        safe_send(
            asunto=f'🚗 Nueva reserva: {reserva.fecha:%d/%m} {reserva.hora_inicio:%H:%M} · {reserva.cliente_nombre} · {reserva.patente}',
            para=admin_emails,
            template='taller/email/nueva_reserva_admin',
            contexto={'reserva': reserva, 'site_url': request.build_absolute_uri('/').rstrip('/')},
            from_alias=getattr(settings, 'EMAIL_AGENDA_FROM', None),
        )

    # Limpio token de sesión — el cliente entra al detalle por URL del email
    request.session.pop('agendar_token', None)

    messages.success(request, '¡Reserva confirmada! Te llegará un correo con los detalles.')
    return redirect('ver_reserva', token=token)


# ─── 5) Reenviar código de email ──────────────────────────────────────────
@never_cache
@require_POST
def reenviar_codigo_view(request):
    from archivo_pietramonte.email_utils import safe_send

    token = request.session.get('agendar_token')
    if not token:
        return redirect('agendar')

    try:
        reserva = Reserva.objects.get(token_hash=hash_token(token))
    except Reserva.DoesNotExist:
        return redirect('agendar')

    if reserva.estado != Reserva.Estado.PENDIENTE_EMAIL:
        return redirect('ver_reserva', token=token)

    codigo = anti_bot.generar_codigo_email(reserva.cliente_email)
    safe_send(
        asunto=f'Código de verificación · {codigo}',
        para=reserva.cliente_email,
        template='taller/email/codigo_verificacion',
        contexto={'codigo': codigo, 'reserva': reserva},
        from_alias=getattr(settings, 'EMAIL_AGENDA_FROM', None),
        reply_to=[settings.EMAIL_REPLY_TO_AGENDA] if settings.EMAIL_REPLY_TO_AGENDA else None,
    )
    messages.info(request, 'Código reenviado. Mirá tu casilla (también spam).')
    return redirect('verificar_email')


# ─── 6) Ver mi reserva ────────────────────────────────────────────────────
@never_cache
@require_GET
def ver_reserva_view(request, token):
    try:
        reserva = Reserva.objects.prefetch_related('items').get(token_hash=hash_token(token))
    except Reserva.DoesNotExist:
        raise Http404

    return render(request, 'taller/mi_reserva.html', {
        'reserva': reserva,
        'token':   token,
        'puede_cancelar': reserva.esta_activa and reserva.fecha >= timezone.localdate(),
    })


# ─── 7) Cancelar mi reserva (cliente) ─────────────────────────────────────
@never_cache
@require_POST
def cancelar_reserva_view(request, token):
    try:
        reserva = Reserva.objects.get(token_hash=hash_token(token))
    except Reserva.DoesNotExist:
        raise Http404

    if not reserva.esta_activa:
        messages.info(request, 'Esta reserva ya estaba cancelada o completada.')
        return redirect('ver_reserva', token=token)

    if reserva.fecha < timezone.localdate():
        messages.error(request, 'No se puede cancelar una reserva pasada.')
        return redirect('ver_reserva', token=token)

    reserva.estado = Reserva.Estado.CANCELADA_CLIENTE
    reserva.cancelada_en = timezone.now()
    reserva.cancelada_por = 'cliente'
    reserva.cancelada_motivo = (request.POST.get('motivo') or '')[:200]
    reserva.save(update_fields=['estado', 'cancelada_en', 'cancelada_por', 'cancelada_motivo'])

    messages.success(request, 'Reserva cancelada. Si querés agendar otra fecha, te esperamos.')
    return redirect('ver_reserva', token=token)


# ─── 8) Confirmar reserva (cliente clickea botón en email reminder) ───────
@never_cache
@require_POST
def confirmar_reserva_token_view(request, token):
    """
    El cliente clickeó "Confirmar mi llegada" en el email reminder. Solo
    actualiza `confirmada_email_en` (no cambia estado si ya estaba confirmada).
    Idempotente: clickear N veces no rompe.
    """
    try:
        reserva = Reserva.objects.get(token_hash=hash_token(token))
    except Reserva.DoesNotExist:
        raise Http404

    if not reserva.esta_activa:
        messages.info(request, 'Esta reserva ya no está activa.')
        return redirect('ver_reserva', token=token)

    reserva.confirmada_email_en = timezone.now()
    if reserva.estado == Reserva.Estado.PENDIENTE_EMAIL:
        reserva.estado = Reserva.Estado.CONFIRMADA_EMAIL
    reserva.save(update_fields=['estado', 'confirmada_email_en'])

    messages.success(request, '¡Confirmación recibida! Te esperamos.')
    return redirect('ver_reserva', token=token)


# ─── Helpers ──────────────────────────────────────────────────────────────
def _valida_patente(p: str) -> bool:
    try:
        patente_validator(p)
        return True
    except ValidationError:
        return False


def _valida_telefono(t: str) -> bool:
    if not t:
        return False
    try:
        telefono_validator(t)
        return True
    except ValidationError:
        return False


def _ofuscar_email(email: str) -> str:
    """foo@bar.com → f***@bar.com (para mostrar en pantalla sin filtrar el local part)."""
    if not email or '@' not in email:
        return email
    local, dom = email.split('@', 1)
    if len(local) <= 2:
        return f'***@{dom}'
    return f'{local[0]}***{local[-1] if len(local) > 3 else ""}@{dom}'
