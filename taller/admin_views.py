"""
Vistas custom del admin de Django para el módulo taller.

  - Panel "Agenda en tiempo real" — auto-refresh cada 30s.
  - Acción 1-click "Confirmar por llamada" desde el panel.

Protegidas con `@permission_required('taller.view_reserva')` — los grupos
creados por `setup_grupos_taller` ya tienen los permisos necesarios. El
middleware Admin2FAMiddleware exige 2FA antes de llegar acá.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .models import Reserva


@staff_member_required
@permission_required('taller.view_reserva', raise_exception=True)
@never_cache
def panel_agenda_view(request):
    """
    Panel custom: hoy + mañana + próximos 7 días + sin confirmar por llamada.
    Auto-refresh cada 30s para sentirse "tiempo real" sin WebSockets.
    """
    hoy     = timezone.localdate()
    manana  = hoy + timedelta(days=1)
    semana  = [hoy + timedelta(days=i) for i in range(7)]

    estados_activos = [
        Reserva.Estado.PENDIENTE_EMAIL,
        Reserva.Estado.CONFIRMADA_EMAIL,
        Reserva.Estado.CONFIRMADA_LLAMADA,
    ]

    # ─── Hoy ────────────────────────────────────────────────────────────
    reservas_hoy = Reserva.objects.filter(fecha=hoy).order_by('hora_inicio').prefetch_related('items')

    # ─── Mañana ─────────────────────────────────────────────────────────
    reservas_manana = Reserva.objects.filter(
        fecha=manana,
        estado__in=estados_activos,
    ).order_by('hora_inicio').prefetch_related('items')

    # ─── Sin confirmar por llamada — próximas 48h ──────────────────────
    pendientes_llamada = Reserva.objects.filter(
        fecha__in=[hoy, manana],
        estado__in=[Reserva.Estado.PENDIENTE_EMAIL, Reserva.Estado.CONFIRMADA_EMAIL],
        confirmada_llamada_en__isnull=True,
    ).order_by('fecha', 'hora_inicio')

    # ─── Resumen de la semana ───────────────────────────────────────────
    resumen_semana = []
    for d in semana:
        qs = Reserva.objects.filter(fecha=d, estado__in=estados_activos)
        resumen_semana.append({
            'fecha':            d,
            'total':            qs.count(),
            'confirmadas':      qs.filter(estado__in=[
                Reserva.Estado.CONFIRMADA_EMAIL,
                Reserva.Estado.CONFIRMADA_LLAMADA,
            ]).count(),
            'pendientes':       qs.filter(estado=Reserva.Estado.PENDIENTE_EMAIL).count(),
            'es_hoy':           d == hoy,
        })

    # ─── Stats globales ─────────────────────────────────────────────────
    stats = {
        'reservas_hoy_total':    reservas_hoy.filter(estado__in=estados_activos).count(),
        'reservas_hoy_completas': reservas_hoy.filter(estado=Reserva.Estado.COMPLETADA).count(),
        'reservas_manana_total': reservas_manana.count(),
        'pendientes_llamada':    pendientes_llamada.count(),
    }

    return render(request, 'taller/admin_agenda.html', {
        'reservas_hoy':       reservas_hoy,
        'reservas_manana':    reservas_manana,
        'pendientes_llamada': pendientes_llamada,
        'resumen_semana':     resumen_semana,
        'stats':              stats,
        'hoy':                hoy,
        'manana':             manana,
        'now':                timezone.localtime(),
        # Lista para iterar estados con su color en template
        'auto_refresh_seg':   30,
    })


@staff_member_required
@permission_required('taller.change_reserva', raise_exception=True)
@require_POST
def confirmar_llamada_view(request, reserva_id):
    """
    Acción 1-click desde el panel: marca la reserva como confirmada-por-llamada
    y guarda quién + cuándo + nota opcional. Idempotente.
    """
    reserva = get_object_or_404(Reserva, id=reserva_id)
    nota    = (request.POST.get('nota') or '')[:500]

    if not reserva.esta_activa:
        messages.warning(request, f'La reserva #{reserva.id} no está activa.')
        return redirect('panel_agenda')

    reserva.estado                  = Reserva.Estado.CONFIRMADA_LLAMADA
    reserva.confirmada_llamada_en   = timezone.now()
    reserva.confirmada_llamada_por  = request.user
    if nota:
        reserva.confirmada_llamada_nota = nota
    reserva.save(update_fields=[
        'estado', 'confirmada_llamada_en', 'confirmada_llamada_por', 'confirmada_llamada_nota',
    ])

    messages.success(request, f'✅ Confirmada por llamada: {reserva.cliente_nombre} ({reserva.fecha} {reserva.hora_inicio:%H:%M})')
    return redirect('panel_agenda')
