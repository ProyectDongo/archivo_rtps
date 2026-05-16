from ._base import (
    portal_login_required, _audit, _usuario_actual, _buzon_actual,
    logger,
)
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import TruncMonth
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from datetime import timedelta
import re

from ..models import (
    Adjunto, ArchivoVinculo, BorradorCorreo, Buzon, CategoriaTema,
    Correo, CorreoEliminado, CorreoLeido, CorreoSnooze,
    Etiqueta, EventoAuditoria, UsuarioPortal, hash_ip,
)
from ..threading import (
    create_thread_for as thread_create_for,
    recompute_thread_cache as thread_recompute,
)
from ..throttle import throttle_user
from ..templatetags.correos_tags import html_a_texto
from .archivos import _archivos_visibles_qs

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
    ).exclude(
        # Soft-delete per-usuario: si existe CorreoEliminado para el usuario
        # actual (en cualquier estado: papelera o purgado), ocultarlo del inbox.
        id__in=CorreoEliminado.objects.filter(usuario=usuario).values('correo_id'),
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

    # ─── Vista de hilos (estilo Gmail) ──────────────────────────────────
    # Toggle: 'hilos' (default, agrupa por thread mostrando solo el ultimo)
    # o 'plana' (lista plana sin agrupación).
    # Auto-disable cuando hay búsqueda de texto activa: si el user busca algo,
    # queremos mostrarle TODAS las coincidencias, no solo la última del hilo.
    vista_pref = (request.COOKIES.get('inbox_vista') or 'hilos').lower()
    vista = (request.GET.get('vista') or vista_pref).lower()
    if vista not in ('hilos', 'plana'):
        vista = 'hilos'
    busqueda_activa = bool(query)
    agrupar_hilos = (vista == 'hilos') and not busqueda_activa

    if agrupar_hilos:
        # Reemplazamos el queryset por uno que devuelve, por cada hilo, solo
        # el correo MÁS RECIENTE (o el más antiguo si orden==asc).
        # Correos sin thread (raros tras el backfill) pasan tal cual.
        latest_per_thread_subq = (
            Correo.objects
            .filter(thread_id=OuterRef('thread_id'), buzon=OuterRef('buzon'))
            .order_by('fecha' if orden == 'asc' else '-fecha')
            .values('id')[:1]
        )
        correos_qs = correos_qs.filter(
            Q(thread__isnull=True) |
            Q(id=Subquery(latest_per_thread_subq))
        ).annotate(thread_count_local=F('thread__count'))
    else:
        correos_qs = correos_qs.annotate(thread_count_local=F('thread__count'))

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

    resp = render(request, 'correos/inbox.html', {
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
        'vista': vista,
        'agrupar_hilos': agrupar_hilos,
    })
    # Persistir la preferencia de vista (hilos/plana) si el user la cambió por URL.
    if request.GET.get('vista') in ('hilos', 'plana'):
        resp.set_cookie('inbox_vista', request.GET['vista'],
                        max_age=60 * 60 * 24 * 365, samesite='Lax')
    return resp


@portal_login_required
def papelera_correos_view(request):
    """
    Vista de la papelera de correos del usuario actual.
    Muestra los Correos con CorreoEliminado.purgado=False — los que están
    "en papelera" (recuperables).
    """
    usuario = _usuario_actual(request)
    if not usuario:
        return redirect('login')

    # Traemos los CorreoEliminado del user (no purgados) + el correo joineado.
    # eliminado_en del record es la fecha que nos importa para ordenar.
    elim_qs = (
        CorreoEliminado.objects
        .filter(usuario=usuario, purgado=False,
                correo__buzon__in=usuario.buzones_visibles())
        .select_related('correo', 'correo__buzon')
        .prefetch_related('correo__etiquetas')
        .order_by('-eliminado_en')
    )

    paginator = Paginator(elim_qs, 50)
    page = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'correos/papelera.html', {
        'page': page,
        'total': paginator.count,
        'buzones_visibles': list(usuario.buzones_visibles()),
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
        # script-src 'self' necesario para el visor de PDF nativo de Chrome
        response['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
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
    buzón. Excluye al propio correo. Ordenado cronológicamente.

    Estrategia:
    1. Si el correo tiene `thread_id` asignado → todos los Correos con el
       mismo thread_id. Cero falsos positivos/negativos.
    2. Si NO tiene thread (correo legacy sin backfill) → fallback a heurística
       histórica: mismo asunto normalizado (case-insensitive).

    NO usa `.only(...)` porque los template tags `correo_iframe` y similares
    leen `cuerpo_html`, `cuerpo_texto`, `tiene_adjunto`, `destinatario`. Si
    los excluís acá, Django hace una query extra por cada uno (N+1). Mejor
    traer todo y prefetch adjuntos.
    """
    if correo.thread_id:
        return (Correo.objects
                .filter(buzon=correo.buzon, thread_id=correo.thread_id)
                .exclude(id=correo.id)
                .order_by('fecha')
                .prefetch_related('adjuntos'))

    norm = _normalizar_asunto(correo.asunto)
    if not norm or len(norm) < 4:
        return Correo.objects.none()

    qs = Correo.objects.filter(buzon=correo.buzon).exclude(id=correo.id)
    qs = qs.filter(
        Q(asunto__iexact=norm) |
        Q(asunto__iendswith=': ' + norm) |
        Q(asunto__iendswith=':' + norm)
    )
    return qs.order_by('fecha').prefetch_related('adjuntos')


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


# ─── Papelera (soft-delete per-usuario) ────────────────────────────────────
def _es_ajax(request) -> bool:
    """
    True si el request viene de fetch/XHR; False si es submit de form clasico.

    Acepta cualquier valor en X-Requested-With (XMLHttpRequest, fetch, etc.) —
    los browsers solo lo setean cuando JS lo pide explícito, así que cualquier
    valor indica fetch/AJAX (PM.post manda 'fetch'). Un form HTML clásico no
    setea ese header.
    """
    return bool(request.headers.get('x-requested-with'))


def _respuesta_papelera(request, mensaje: str, ok: bool = True, **extras):
    """Helper común: AJAX→JSON. Form clásico→messages + redirect a inbox."""
    if _es_ajax(request):
        return JsonResponse({'ok': ok, **extras})
    if ok:
        messages.success(request, mensaje)
    else:
        messages.error(request, mensaje)
    return redirect('inbox')


@portal_login_required
@require_POST
def correo_eliminar_view(request, correo_id):
    """
    POST → mueve el correo a la papelera del usuario actual (soft-delete).
    Si ya estaba purgado, lo "des-purga" para que vuelva a la papelera
    (caso edge — pero coherente con la semántica del modelo).
    Idempotente: re-llamarlo no rompe nada.

    AJAX → JSON {ok: True}. Form clásico → redirect a inbox con messages.
    """
    usuario, correo = _correo_si_visible(request, correo_id)
    rec, creado = CorreoEliminado.objects.get_or_create(
        usuario=usuario, correo=correo,
        defaults={'purgado': False},
    )
    if not creado and rec.purgado:
        rec.purgado = False
        rec.save(update_fields=['purgado'])
    return _respuesta_papelera(request, 'Correo movido a la papelera.')


@portal_login_required
@require_POST
def correo_restaurar_view(request, correo_id):
    """
    POST → saca el correo de la papelera (vuelve a la bandeja del usuario).
    Borra el record de CorreoEliminado.
    """
    usuario, correo = _correo_si_visible(request, correo_id)
    CorreoEliminado.objects.filter(usuario=usuario, correo=correo).delete()
    if _es_ajax(request):
        return JsonResponse({'ok': True})
    messages.success(request, 'Correo restaurado a la bandeja.')
    return redirect('papelera_correos')


@portal_login_required
@require_POST
def correo_eliminar_permanente_view(request, correo_id):
    """
    POST → marca el correo como purgado para el usuario actual. Sale de la
    papelera y no vuelve al inbox. El Correo y sus adjuntos NO se borran de
    la DB — otros usuarios del mismo buzón siguen viéndolos.
    """
    usuario, correo = _correo_si_visible(request, correo_id)
    rec, _ = CorreoEliminado.objects.get_or_create(
        usuario=usuario, correo=correo,
        defaults={'purgado': True},
    )
    if not rec.purgado:
        rec.purgado = True
        rec.save(update_fields=['purgado'])
    if _es_ajax(request):
        return JsonResponse({'ok': True})
    messages.success(request, 'Correo eliminado definitivamente de tu vista.')
    return redirect('papelera_correos')


@portal_login_required
@require_POST
def vaciar_papelera_view(request):
    """
    POST → marca como purgados TODOS los CorreoEliminado del usuario en
    estado papelera (purgado=False). Vacía la papelera del usuario actual.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        raise Http404
    n = CorreoEliminado.objects.filter(
        usuario=usuario, purgado=False
    ).update(purgado=True)
    if _es_ajax(request):
        return JsonResponse({'ok': True, 'purgados': n})
    messages.success(request, f'Papelera vaciada: {n} correo{"s" if n != 1 else ""} eliminado{"s" if n != 1 else ""} definitivamente.')
    return redirect('papelera_correos')


@portal_login_required
@require_POST
def correo_bulk_eliminar_view(request):
    """
    POST {ids: "1,2,3"} → mueve varios correos a la papelera del usuario.
    Idempotente. Devuelve cantidad afectada.
    """
    usuario = _usuario_actual(request)
    if not usuario:
        raise Http404
    raw_ids = (request.POST.get('ids') or '').strip()
    if not raw_ids:
        return JsonResponse({'ok': False, 'error': 'sin ids'}, status=400)
    try:
        ids = [int(x) for x in raw_ids.split(',') if x.strip().isdigit()][:200]
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'ids invalidos'}, status=400)
    if not ids:
        return JsonResponse({'ok': False, 'error': 'sin ids validos'}, status=400)

    # Filtrar a correos visibles para el user.
    buzones_visibles = usuario.buzones_visibles()
    correos = Correo.objects.filter(id__in=ids, buzon__in=buzones_visibles)
    creados = 0
    for c in correos:
        _, creado = CorreoEliminado.objects.get_or_create(
            usuario=usuario, correo=c,
            defaults={'purgado': False},
        )
        if creado:
            creados += 1
    return JsonResponse({'ok': True, 'afectados': creados})


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


# ─── Acciones masivas (multi-select) ────────────────────────────────────────
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
        from ..models import Buzon
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
