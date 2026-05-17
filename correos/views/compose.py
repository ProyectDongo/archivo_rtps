from ._base import (
    portal_login_required, _audit, _usuario_actual, _buzon_actual,
    _get_ip, logger,
)
import re
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from datetime import timedelta

from ..models import (
    Adjunto, BorradorAdjunto, BorradorCorreo, Buzon, Correo,
    CorreoEnviado, CorreoLeido, Etiqueta, ReenvioCorreo, UsuarioPortal, hash_ip,
)
from ..threading import (
    create_thread_for as thread_create_for,
    recompute_thread_cache as thread_recompute,
)
from ..throttle import throttle_user
from ..templatetags.correos_tags import html_a_texto


# ─── Reenvío de correos al exterior ─────────────────────────────────────────
# Cualquier UsuarioPortal puede reenviar correos de los buzones que ve.
# Rate limit: 30 reenvíos/día normales, 100/día admins. Audit completo en
# `ReenvioCorreo`. From=EMAIL_REENVIO_FROM (típicamente la cuenta interna),
# Reply-To=email del usuario portal que reenvía → respuestas vuelven al equipo.
REENVIO_RL_HORAS    = 24
REENVIO_LIMIT_USER  = 30
REENVIO_LIMIT_ADMIN = 100
REENVIO_MAX_DEST    = 30        # max emails por reenvío
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


# ─── Autocompletado de contactos ────────────────────────────────────────────
# Regex compartido entre el endpoint /intranet/contactos/ y los parsers.
# Acepta "Nombre <email@x.cl>" y "email@x.cl" pelado.
_EMAIL_REGEX = re.compile(r'([\w.+-]+@[\w-]+\.[\w.-]+)')
_NAME_EMAIL_REGEX = re.compile(r'^\s*"?([^"<>]+?)"?\s*<\s*([\w.+-]+@[\w-]+\.[\w.-]+)\s*>\s*$')


def _split_name_email(raw: str) -> tuple[str, str]:
    """('Ana López <ana@x.cl>',) → ('Ana López', 'ana@x.cl'). Si no hay nombre, devuelve ('', email)."""
    if not raw:
        return ('', '')
    m = _NAME_EMAIL_REGEX.match(raw)
    if m:
        return (m.group(1).strip(), m.group(2).lower())
    em = _EMAIL_REGEX.search(raw)
    return ('', em.group(1).lower()) if em else ('', '')


@portal_login_required
@require_http_methods(['GET'])
@throttle_user('contactos', per_minute=120)
def contactos_view(request):
    """
    Autocompletado de contactos del buzón actual.

    GET /intranet/contactos/?q=<prefix>

    Devuelve top 10 contactos del buzón cuyo nombre o email empieza con `q`,
    rankeados por frecuencia de aparición en correos recibidos, enviados,
    respuestas y reenvíos. Solo dentro del buzón actual (no leak entre buzones).

    Respuesta: {"contactos": [{"email": "...", "nombre": "...", "freq": N}, ...]}
    """
    from collections import Counter

    q_raw = (request.GET.get('q') or '').strip().lower()
    if len(q_raw) < 1:
        return JsonResponse({'contactos': []})
    # Evita explosiones: prefix muy corto se limita a 2 chars de match útiles
    if len(q_raw) > 80:
        q_raw = q_raw[:80]

    usuario = _usuario_actual(request)
    buzon = _buzon_actual(request, usuario)
    if not buzon:
        return JsonResponse({'contactos': []})

    # Cache: cualquier corrección de contactos llega vía nuevo correo IMAP que
    # tarda al menos 10 min en aparecer (cron del sync). 5 min de TTL es seguro.
    cache_key = f'contactos:{buzon.id}:{q_raw}'
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'contactos': cached})

    counter: Counter = Counter()
    nombres: dict[str, str] = {}      # email → mejor nombre encontrado

    def _ingest(raw: str):
        # Separa por coma o ; — los campos del modelo guardan listas así.
        for part in (raw or '').replace(';', ',').split(','):
            nombre, email = _split_name_email(part)
            if not email:
                continue
            # Match por substring en email o nombre (estilo Gmail).
            if q_raw not in email and (not nombre or q_raw not in nombre.lower()):
                continue
            counter[email] += 1
            # Preserva el nombre más informativo (el primero no vacío gana).
            if nombre and not nombres.get(email):
                nombres[email] = nombre

    # 1) Remitentes de correos del buzón (limitado a 2000 más recientes para no escanear todo).
    remitentes = (
        Correo.objects.filter(buzon=buzon)
        .exclude(remitente='')
        .order_by('-fecha')
        .values_list('remitente', flat=True)[:2000]
    )
    for r in remitentes:
        _ingest(r)

    # 2) Destinatarios de correos en carpeta "enviados" (legado del .mbox importado).
    dests = (
        Correo.objects.filter(buzon=buzon, tipo_carpeta=Correo.Carpeta.ENVIADOS)
        .exclude(destinatario='')
        .order_by('-fecha')
        .values_list('destinatario', flat=True)[:2000]
    )
    for d in dests:
        _ingest(d)

    # 3) Auditoría de envíos desde el portal (más fresca, refleja el uso real).
    enviados = (
        CorreoEnviado.objects.filter(buzon=buzon, exito=True)
        .order_by('-enviado_en')
        .values_list('destinatarios', 'cc')[:1000]
    )
    for to, cc in enviados:
        _ingest(to)
        _ingest(cc)

    # 4) Reenvíos (correos archivados forward a externos).
    reenvios = (
        ReenvioCorreo.objects.filter(correo__buzon=buzon, exito=True)
        .order_by('-enviado_en')
        .values_list('destinatarios', flat=True)[:1000]
    )
    for d in reenvios:
        _ingest(d)

    # Top 10 por frecuencia. Excluye el propio email del buzón (no tiene sentido sugerirse a uno mismo).
    propio = (buzon.email or '').lower()
    items = []
    for email, freq in counter.most_common(20):
        if email == propio:
            continue
        items.append({
            'email': email,
            'nombre': nombres.get(email, ''),
            'freq': freq,
        })
        if len(items) >= 10:
            break

    cache.set(cache_key, items, 300)
    return JsonResponse({'contactos': items})


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

    from archivo.email_utils import safe_send

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
RESP_MAX_DEST    = 30        # To + Cc combinados
RESP_MAX_BODY    = 50000     # 50 KB de texto plano del usuario


def _enviados_recientes(usuario: UsuarioPortal) -> int:
    """Cantidad de respuestas/composiciones del usuario en las últimas RESP_RL_HORAS."""
    desde = timezone.now() - timedelta(hours=RESP_RL_HORAS)
    return CorreoEnviado.objects.filter(usuario=usuario, enviado_en__gte=desde).count()


def _brand_email_ctx() -> dict:
    """Variables de marca para los templates de email saliente."""
    logo_url = getattr(settings, 'FIRMA_LOGO_URL', '')
    firma_logo_url = getattr(settings, 'FIRMA_LOGO_FIRMA_URL', '') or logo_url
    return {
        'brand_logo_url':       logo_url,
        'brand_firma_logo_url': firma_logo_url,
        'brand_color':          getattr(settings, 'BRAND_PRIMARY_COLOR', '#1F7A33'),
        'brand_company_name':   getattr(settings, 'BRAND_COMPANY_NAME', 'Río San Pedro RT'),
    }


def _from_alias_buzon(buzon: Buzon) -> str:
    """Construye el From del envío. Usa el nombre del buzón si está y es distinto al email."""
    nombre = (buzon.nombre or '').strip()
    if nombre and nombre.lower() != (buzon.email or '').lower():
        return f'{nombre} <{buzon.email}>'
    return buzon.email or ''


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

    from archivo.email_utils import safe_send

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
    new_msg_id = make_msgid(domain='rtriosanpedro.cl')
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
            # El editor Quill produce HTML (`<p>...</p>`). Guardamos el HTML
            # en `cuerpo_html` para que el preview lo rendee igual que Gmail,
            # y derivamos `cuerpo_texto` con strip de tags para búsqueda y
            # fallback a clientes que no entienden HTML.
            _es_html = bool(cuerpo) and ('<' in cuerpo and '>' in cuerpo)
            # Thread: hereda del correo original (este es Reply).
            _thread = correo.thread
            if _thread is None:
                # El original todavía no tenía thread asignado (backlog
                # legacy). Lo creamos ahora con el original como raíz.
                _thread = thread_create_for(correo)
                correo.thread = _thread
                correo.save(update_fields=['thread'])
            sent_correo = Correo.objects.create(
                buzon=correo.buzon,
                tipo_carpeta=Correo.Carpeta.ENVIADOS,
                mensaje_id=new_msg_id[:500],
                in_reply_to=(correo.mensaje_id or '')[:500],
                references=(correo.references + ' ' + (correo.mensaje_id or '')).strip()[:5000],
                thread=_thread,
                remitente=_from_alias_buzon(correo.buzon)[:500],
                destinatario=', '.join(to_addrs + cc_addrs)[:1000],
                asunto=asunto[:1000],
                fecha=timezone.now(),
                cuerpo_texto=html_a_texto(cuerpo) if _es_html else (cuerpo or ''),
                cuerpo_html=cuerpo if _es_html else '',
                tiene_adjunto=False,
            )
            thread_recompute(_thread)
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

    from archivo.email_utils import safe_send

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

    new_msg_id = make_msgid(domain='rtriosanpedro.cl')
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
            _es_html = bool(cuerpo) and ('<' in cuerpo and '>' in cuerpo)
            # Thread: si el borrador era responder/responder-todos, hereda
            # del original; si era compose nuevo o reenvío externo, abre
            # un thread propio.
            _orig = b.correo_original
            _is_reply = (_orig is not None and b.modo in (
                BorradorCorreo.Modo.RESPONDER, BorradorCorreo.Modo.RESPONDER_TODOS,
            ))
            if _is_reply:
                _thread = _orig.thread
                if _thread is None:
                    _thread = thread_create_for(_orig)
                    _orig.thread = _thread
                    _orig.save(update_fields=['thread'])
                _irt = (_orig.mensaje_id or '')[:500]
                _refs = ((_orig.references or '') + ' ' + (_orig.mensaje_id or '')).strip()[:5000]
            else:
                _thread = None  # se setea abajo después del create
                _irt = ''
                _refs = ''
            sent_correo = Correo.objects.create(
                buzon=buzon,
                tipo_carpeta=Correo.Carpeta.ENVIADOS,
                mensaje_id=new_msg_id[:500],
                in_reply_to=_irt,
                references=_refs,
                thread=_thread,
                remitente=_from_alias_buzon(buzon)[:500],
                destinatario=', '.join(to_addrs + cc_addrs)[:1000],
                asunto=asunto[:1000],
                fecha=timezone.now(),
                cuerpo_texto=html_a_texto(cuerpo) if _es_html else (cuerpo or ''),
                cuerpo_html=cuerpo if _es_html else '',
                tiene_adjunto=bool(adjuntos_draft),
            )
            if _thread is None:
                _thread = thread_create_for(sent_correo)
                sent_correo.thread = _thread
                sent_correo.save(update_fields=['thread'])
            else:
                thread_recompute(_thread)
            CorreoLeido.objects.get_or_create(usuario=usuario, correo=sent_correo)
            # Guardar adjuntos en DB para que aparezcan en la vista de enviados
            for nombre, contenido, mime in adjuntos_draft:
                adj = Adjunto(
                    correo=sent_correo,
                    nombre_original=nombre,
                    mime_type=mime[:200],
                    tamano_bytes=len(contenido),
                )
                adj.archivo.save(nombre, ContentFile(contenido), save=False)
                adj.save()
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

    from archivo.email_utils import safe_send

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

    new_msg_id = make_msgid(domain='rtriosanpedro.cl')
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
            _es_html = bool(cuerpo) and ('<' in cuerpo and '>' in cuerpo)
            # Compose nuevo siempre abre un thread propio (no es reply).
            sent_correo = Correo.objects.create(
                buzon=buzon,
                tipo_carpeta=Correo.Carpeta.ENVIADOS,
                mensaje_id=new_msg_id[:500],
                remitente=_from_alias_buzon(buzon)[:500],
                destinatario=', '.join(to_addrs + cc_addrs)[:1000],
                asunto=asunto[:1000],
                fecha=timezone.now(),
                cuerpo_texto=html_a_texto(cuerpo) if _es_html else (cuerpo or ''),
                cuerpo_html=cuerpo if _es_html else '',
                tiene_adjunto=bool(archivos_para_persistir),
            )
            _thread = thread_create_for(sent_correo)
            sent_correo.thread = _thread
            sent_correo.save(update_fields=['thread'])
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


