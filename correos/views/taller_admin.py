"""
Vistas para gestión del taller desde el escritorio del portal (no Django admin).

Cubre 3 secciones:

1. **Servicios y Repuestos** (`ItemCatalogo` del taller)
   - CRUD: crear, listar, editar, eliminar (soft-delete via campo `activo`)
   - Filtros por categoría y tipo

2. **Agenda de reservas** (`Reserva`)
   - Vista calendario mensual de las reservas
   - Detalle de cada reserva
   - Acción "confirmar por llamada" (marca el campo confirmada_llamada_*)
   - Acción "cancelar"

Permisos: admins siempre, o usuarios con `UsuarioPortal.puede_taller=True`.
URL base: `/intranet/taller/...`
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from taller.models import ItemCatalogo, Reserva, BloqueoCalendario

from ..models import UsuarioPortal
from ._base import _audit, _usuario_actual, portal_login_required


def _puede_taller(usuario: UsuarioPortal | None) -> bool:
    """True si el usuario puede gestionar el módulo taller."""
    return bool(usuario and (usuario.es_admin or usuario.puede_taller))


# ────────────────────────────────────────────────────────── Catálogo (items) ──

@portal_login_required
@never_cache
def taller_items_list_view(request, tipo: str):
    """
    Lista de servicios o repuestos del catálogo.
    `tipo` es 'servicio' o 'repuesto' — viene de la URL.
    """
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        messages.error(request, 'No tenés permisos para gestionar el taller.')
        return redirect('escritorio')

    if tipo not in ('servicio', 'repuesto'):
        raise Http404

    items = ItemCatalogo.objects.filter(tipo=tipo).order_by('orden', 'nombre')

    # Filtros opcionales
    categoria = request.GET.get('categoria') or ''
    if categoria:
        items = items.filter(categoria=categoria)
    activo_filtro = request.GET.get('activo')
    if activo_filtro == 'si':
        items = items.filter(activo=True)
    elif activo_filtro == 'no':
        items = items.filter(activo=False)

    # Categorías para el dropdown
    if tipo == 'servicio':
        cats = [(c.value, c.label) for c in ItemCatalogo.Categoria
                if not c.value.startswith('rep_') and c.value != 'otros']
        titulo = 'Servicios'
    else:
        cats = [(c.value, c.label) for c in ItemCatalogo.Categoria
                if c.value.startswith('rep_') or c.value == 'otros']
        titulo = 'Repuestos'

    return render(request, 'correos/taller_items_list.html', {
        'usuario':    usuario,
        'items':      items,
        'tipo':       tipo,
        'titulo':     titulo,
        'categorias': cats,
        'categoria_actual': categoria,
        'activo_filtro': activo_filtro,
        'total':      items.count(),
    })


@portal_login_required
@require_http_methods(['GET', 'POST'])
@never_cache
def taller_item_form_view(request, tipo: str, item_id: int | None = None):
    """Crear o editar un item del catálogo (servicio o repuesto)."""
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        raise Http404
    if tipo not in ('servicio', 'repuesto'):
        raise Http404

    item = None
    if item_id:
        item = get_object_or_404(ItemCatalogo, id=item_id, tipo=tipo)

    if request.method == 'POST':
        nombre      = (request.POST.get('nombre') or '').strip()[:120]
        descripcion = (request.POST.get('descripcion') or '').strip()[:2000]
        categoria   = (request.POST.get('categoria') or '').strip()[:30]
        precio_raw  = (request.POST.get('precio_referencia_clp') or '0').strip()
        duracion_raw = (request.POST.get('duracion_min') or '30').strip()
        disponibilidad = (request.POST.get('disponibilidad') or 'en_stock').strip()
        marca_repuesto = (request.POST.get('marca_repuesto') or '').strip() if tipo == 'repuesto' else ''
        destacado   = bool(request.POST.get('destacado'))
        activo      = bool(request.POST.get('activo'))
        orden_raw   = (request.POST.get('orden') or '0').strip()
        icono       = (request.POST.get('icono_lucide') or 'wrench').strip()[:40]

        if not nombre:
            messages.error(request, 'El nombre es obligatorio.')
            return redirect(request.path)

        try:
            precio = max(0, int(precio_raw))
            duracion = max(0, int(duracion_raw))
            orden = int(orden_raw)
        except (TypeError, ValueError):
            messages.error(request, 'Precio, duración y orden deben ser números.')
            return redirect(request.path)

        # Validar categoría
        categorias_validas = {c.value for c in ItemCatalogo.Categoria}
        if categoria not in categorias_validas:
            categoria = ItemCatalogo.Categoria.MANTENCION if tipo == 'servicio' else ItemCatalogo.Categoria.OTROS

        data = {
            'nombre': nombre, 'descripcion': descripcion, 'categoria': categoria,
            'tipo': tipo, 'precio_referencia_clp': precio, 'duracion_min': duracion,
            'disponibilidad': disponibilidad, 'marca_repuesto': marca_repuesto,
            'destacado': destacado, 'activo': activo, 'orden': orden,
            'icono_lucide': icono,
        }

        if item:
            for k, v in data.items():
                setattr(item, k, v)
            item.save()
            _audit(request, 'taller_item_editar', 'item_catalogo', item.id, nombre=nombre, tipo=tipo)
            messages.success(request, f'{tipo.capitalize()} "{nombre}" actualizado.')
        else:
            item = ItemCatalogo.objects.create(**data)
            _audit(request, 'taller_item_crear', 'item_catalogo', item.id, nombre=nombre, tipo=tipo)
            messages.success(request, f'{tipo.capitalize()} "{nombre}" creado.')

        return redirect('taller_items_list', tipo=tipo)

    # GET: render form
    if tipo == 'servicio':
        cats = [(c.value, c.label) for c in ItemCatalogo.Categoria
                if not c.value.startswith('rep_') and c.value != 'otros']
    else:
        cats = [(c.value, c.label) for c in ItemCatalogo.Categoria
                if c.value.startswith('rep_') or c.value == 'otros']

    return render(request, 'correos/taller_item_form.html', {
        'usuario': usuario,
        'item':    item,
        'tipo':    tipo,
        'categorias': cats,
        'disponibilidades': ItemCatalogo.Disponibilidad.choices,
        'marcas': ItemCatalogo.Marca.choices,
    })


@portal_login_required
@require_POST
def taller_item_eliminar_view(request, item_id: int):
    """Soft-delete: marca como inactivo. Si querés borrar de verdad, desde Django admin."""
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        raise Http404
    item = get_object_or_404(ItemCatalogo, id=item_id)
    tipo = item.tipo
    nombre = item.nombre
    item.activo = False
    item.save(update_fields=['activo', 'actualizado'])
    _audit(request, 'taller_item_desactivar', 'item_catalogo', item.id, nombre=nombre)
    messages.success(request, f'{tipo.capitalize()} "{nombre}" desactivado (no aparece más en el catálogo público).')
    return redirect('taller_items_list', tipo=tipo)


@portal_login_required
@require_POST
def taller_item_toggle_view(request, item_id: int):
    """Toggle activo/inactivo via AJAX."""
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        raise Http404
    item = get_object_or_404(ItemCatalogo, id=item_id)
    item.activo = not item.activo
    item.save(update_fields=['activo', 'actualizado'])
    _audit(request, 'taller_item_toggle', 'item_catalogo', item.id, activo=item.activo)
    return JsonResponse({'ok': True, 'activo': item.activo})


# ────────────────────────────────────────────────────────────── Agenda ──

@portal_login_required
@never_cache
def taller_agenda_view(request):
    """
    Calendario mensual de reservas. Muestra una grilla del mes con las
    reservas de cada día.

    Query params:
      ?year=2026&month=5  → mes específico (default: hoy)
    """
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        messages.error(request, 'No tenés permisos para ver la agenda del taller.')
        return redirect('escritorio')

    hoy = timezone.localdate()
    try:
        year  = int(request.GET.get('year') or hoy.year)
        month = int(request.GET.get('month') or hoy.month)
        if month < 1 or month > 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = hoy.year, hoy.month

    # Primer día del mes y último día
    primer_dia = date(year, month, 1)
    ultimo_dia = date(year, month, calendar.monthrange(year, month)[1])

    # Reservas del mes
    reservas = (
        Reserva.objects
        .filter(fecha__gte=primer_dia, fecha__lte=ultimo_dia)
        .exclude(estado__in=[Reserva.Estado.CANCELADA_CLIENTE, Reserva.Estado.CANCELADA_TALLER])
        .order_by('fecha', 'hora_inicio')
        .prefetch_related('items')
    )

    # Agrupar por fecha para la grilla
    reservas_por_fecha: dict[date, list[Reserva]] = {}
    for r in reservas:
        reservas_por_fecha.setdefault(r.fecha, []).append(r)

    # Bloqueos del mes
    bloqueos = BloqueoCalendario.objects.filter(
        fecha__gte=primer_dia, fecha__lte=ultimo_dia,
    ).values_list('fecha', 'motivo')
    bloqueos_dict = {f: m for f, m in bloqueos}

    # Construir grilla del mes (semanas)
    cal = calendar.Calendar(firstweekday=0)  # 0 = lunes
    semanas = []
    for week in cal.monthdatescalendar(year, month):
        fila = []
        for d in week:
            es_otro_mes = d.month != month
            fila.append({
                'fecha':       d,
                'dia':         d.day,
                'es_otro_mes': es_otro_mes,
                'es_hoy':      d == hoy,
                'reservas':    reservas_por_fecha.get(d, []),
                'bloqueo':     bloqueos_dict.get(d),
            })
        semanas.append(fila)

    # Navegación
    prev_month = (primer_dia - timedelta(days=1)).replace(day=1)
    next_month_day = (ultimo_dia + timedelta(days=1))

    # Stats del mes
    stats = {
        'total':       reservas.count(),
        'pendientes':  reservas.filter(estado=Reserva.Estado.PENDIENTE_EMAIL).count(),
        'confirmadas': reservas.filter(
            estado__in=[Reserva.Estado.CONFIRMADA_EMAIL, Reserva.Estado.CONFIRMADA_LLAMADA],
        ).count(),
        'completadas': reservas.filter(estado=Reserva.Estado.COMPLETADA).count(),
    }

    return render(request, 'correos/taller_agenda.html', {
        'usuario':   usuario,
        'year':      year,
        'month':     month,
        'mes_nombre': primer_dia.strftime('%B').capitalize(),
        'semanas':   semanas,
        'prev_year': prev_month.year,
        'prev_month': prev_month.month,
        'next_year': next_month_day.year,
        'next_month': next_month_day.month,
        'hoy':       hoy,
        'stats':     stats,
    })


@portal_login_required
@never_cache
def taller_reserva_detalle_view(request, reserva_id: int):
    """Detalle de una reserva — ver items, datos cliente, cambiar estado."""
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        raise Http404
    reserva = get_object_or_404(Reserva.objects.prefetch_related('items'), id=reserva_id)
    return render(request, 'correos/taller_reserva_detalle.html', {
        'usuario': usuario,
        'reserva': reserva,
        'estados': Reserva.Estado.choices,
    })


@portal_login_required
@require_POST
def taller_reserva_confirmar_view(request, reserva_id: int):
    """Marcar reserva como confirmada por llamada."""
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        raise Http404
    reserva = get_object_or_404(Reserva, id=reserva_id)
    nota = (request.POST.get('nota') or '').strip()[:500]

    reserva.estado = Reserva.Estado.CONFIRMADA_LLAMADA
    reserva.confirmada_llamada_en = timezone.now()
    reserva.confirmada_llamada_nota = nota
    # Note: confirmada_llamada_por es FK a auth_user (Django), no UsuarioPortal.
    # Lo dejamos null por ahora. Si se necesita audit, queda en EventoAuditoria.
    reserva.save(update_fields=[
        'estado', 'confirmada_llamada_en', 'confirmada_llamada_nota',
    ])
    _audit(request, 'taller_reserva_confirmar', 'reserva', reserva.id,
           cliente=reserva.cliente_email, fecha=str(reserva.fecha))
    messages.success(request, f'Reserva de {reserva.cliente_nombre} confirmada.')
    return redirect('taller_reserva_detalle', reserva_id=reserva.id)


@portal_login_required
@require_POST
def taller_reserva_cancelar_view(request, reserva_id: int):
    """Cancelar una reserva."""
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        raise Http404
    reserva = get_object_or_404(Reserva, id=reserva_id)
    motivo = (request.POST.get('motivo') or '').strip()[:200]

    reserva.estado = Reserva.Estado.CANCELADA_TALLER
    reserva.cancelada_en = timezone.now()
    reserva.cancelada_por = usuario.email[:120]
    reserva.cancelada_motivo = motivo
    reserva.save(update_fields=[
        'estado', 'cancelada_en', 'cancelada_por', 'cancelada_motivo',
    ])
    _audit(request, 'taller_reserva_cancelar', 'reserva', reserva.id,
           cliente=reserva.cliente_email, motivo=motivo)
    messages.success(request, f'Reserva de {reserva.cliente_nombre} cancelada.')
    return redirect('taller_agenda')


@portal_login_required
@require_POST
def taller_reserva_completar_view(request, reserva_id: int):
    """Marcar reserva como completada (vino y se atendió)."""
    usuario = _usuario_actual(request)
    if not _puede_taller(usuario):
        raise Http404
    reserva = get_object_or_404(Reserva, id=reserva_id)
    reserva.estado = Reserva.Estado.COMPLETADA
    reserva.save(update_fields=['estado'])
    _audit(request, 'taller_reserva_completar', 'reserva', reserva.id,
           cliente=reserva.cliente_email)
    messages.success(request, f'Reserva de {reserva.cliente_nombre} marcada como completada.')
    return redirect('taller_reserva_detalle', reserva_id=reserva.id)
