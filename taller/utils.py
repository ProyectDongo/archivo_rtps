"""
Helpers de slots, disponibilidad y tokens públicos.

Slots:
  El taller atiende en bloques de 30 min entre 9:00 y 18:30 (último auto inicia
  18:00, termina 18:30 con diagnóstico). Almuerzo bloqueado 13:00-14:00. Lun-Vie.
  Capacidad: 1 auto por slot (recepción secuencial — adentro se trabaja en paralelo).

Disponibilidad:
  Un slot está OCUPADO si hay una Reserva en estado activo (pendiente_email,
  confirmada_email o confirmada_llamada) en esa fecha+hora. Cancelaciones y
  no-show liberan el slot.

Tokens:
  Cada Reserva tiene un token público de 256 bits (urlsafe). Lo enviamos
  por email; en BD guardamos solo SHA-256. Para buscar una reserva por
  token plano: `Reserva.objects.get(token_hash=hash_token(plano))`.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone

from .models import (
    DIAS_LABORALES,
    HORA_ALMUERZO_FIN,
    HORA_ALMUERZO_INI,
    HORA_FIN_TALLER,
    HORA_INICIO_TALLER,
    SLOT_MINUTOS,
    BloqueoCalendario,
    Reserva,
)


# ─── Generación de slots de un día ─────────────────────────────────────────
def _todos_los_slots() -> list[time]:
    """Lista de horarios slot del día, sin considerar fecha ni reservas."""
    slots = []
    actual = HORA_INICIO_TALLER
    while actual < HORA_FIN_TALLER:
        # Saltea bloque de almuerzo
        if HORA_ALMUERZO_INI <= actual < HORA_ALMUERZO_FIN:
            actual = (datetime.combine(date.today(), actual)
                      + timedelta(minutes=SLOT_MINUTOS)).time()
            continue
        slots.append(actual)
        actual = (datetime.combine(date.today(), actual)
                  + timedelta(minutes=SLOT_MINUTOS)).time()
    return slots


def es_dia_laboral(fecha: date) -> tuple[bool, str]:
    """
    Devuelve (laboral, motivo). Si no es laboral, motivo explica por qué.
    """
    if fecha < timezone.localdate():
        return (False, 'Fecha pasada')
    if fecha.weekday() not in DIAS_LABORALES:
        return (False, 'Fin de semana — el taller no atiende sábado ni domingo')

    bloqueo = BloqueoCalendario.objects.filter(fecha=fecha, activo=True).first()
    if bloqueo:
        return (False, f'Cerrado: {bloqueo.motivo}')

    return (True, '')


def slots_de_la_fecha(fecha: date) -> list[dict]:
    """
    Devuelve lista de slots para una fecha:
      [{'hora': '09:00', 'disponible': True}, {'hora': '09:30', 'disponible': False}, ...]

    Si la fecha no es laboral (feriado/finde/pasada), devuelve [].
    """
    laboral, _motivo = es_dia_laboral(fecha)
    if not laboral:
        return []

    # Reservas activas en esa fecha (las que ocupan slot)
    activas = set(
        Reserva.objects.filter(
            fecha=fecha,
            estado__in=[
                Reserva.Estado.PENDIENTE_EMAIL,
                Reserva.Estado.CONFIRMADA_EMAIL,
                Reserva.Estado.CONFIRMADA_LLAMADA,
            ],
        ).values_list('hora_inicio', flat=True)
    )

    ahora = timezone.localtime()
    es_hoy = fecha == ahora.date()

    out = []
    for h in _todos_los_slots():
        # Si es hoy, los slots ya pasados no se ofrecen
        if es_hoy and h <= ahora.time():
            continue
        out.append({
            'hora':       h.strftime('%H:%M'),
            'disponible': h not in activas,
        })
    return out


def fechas_proximas(dias: int = 28) -> list[date]:
    """Próximas `dias` fechas calendario desde hoy (incluye finde/feriados)."""
    hoy = timezone.localdate()
    return [hoy + timedelta(days=i) for i in range(dias)]


def fechas_disponibles_proximas(dias: int = 28) -> list[dict]:
    """
    Lista de próximos `dias` con info de disponibilidad para el calendario público.
    Devuelve:
      [{'fecha': date, 'es_laboral': bool, 'motivo': '', 'slots_libres': N}, ...]
    """
    hoy = timezone.localdate()
    inicio = hoy
    fin    = hoy + timedelta(days=dias - 1)

    # Pre-cargar bloqueos del rango
    bloqueos = {
        b.fecha: b.motivo for b in
        BloqueoCalendario.objects.filter(fecha__range=(inicio, fin), activo=True)
    }

    # Pre-cargar reservas activas del rango (para contar libres)
    reservas_por_fecha = {}
    for fecha_, hora in Reserva.objects.filter(
        fecha__range=(inicio, fin),
        estado__in=[
            Reserva.Estado.PENDIENTE_EMAIL,
            Reserva.Estado.CONFIRMADA_EMAIL,
            Reserva.Estado.CONFIRMADA_LLAMADA,
        ],
    ).values_list('fecha', 'hora_inicio'):
        reservas_por_fecha.setdefault(fecha_, set()).add(hora)

    total_slots_dia = len(_todos_los_slots())

    out = []
    for d in fechas_proximas(dias):
        if d.weekday() not in DIAS_LABORALES:
            out.append({'fecha': d, 'es_laboral': False, 'motivo': 'Fin de semana', 'slots_libres': 0})
            continue
        if d in bloqueos:
            out.append({'fecha': d, 'es_laboral': False, 'motivo': bloqueos[d], 'slots_libres': 0})
            continue
        ocupadas = len(reservas_por_fecha.get(d, set()))
        out.append({
            'fecha':        d,
            'es_laboral':   True,
            'motivo':       '',
            'slots_libres': max(0, total_slots_dia - ocupadas),
        })
    return out


def mes_disponibilidad(year: int, month: int) -> dict:
    """
    Devuelve el calendario completo de un mes para la vista pública /agendar/.

    Estructura:
      {
        'year': 2026, 'month': 5, 'mes_nombre': 'mayo',
        'prev_year': 2026, 'prev_month': 4,
        'next_year': 2026, 'next_month': 6,
        'semanas': [
          [dia, dia, dia, dia, dia, dia, dia],  # cada lista = una semana lun-dom
          ...
        ],
      }

    Cada `dia` tiene los mismos campos que `fechas_disponibles_proximas` más:
      - `dia`: número del día (1-31)
      - `es_otro_mes`: True si pertenece al mes anterior/siguiente (relleno de grilla)
      - `es_hoy`: True si es la fecha de hoy
      - `es_pasado`: True si la fecha ya pasó
    """
    import calendar as _cal
    from datetime import date

    hoy = timezone.localdate()
    primer_dia = date(year, month, 1)
    ultimo_dia = date(year, month, _cal.monthrange(year, month)[1])

    # Pre-cargar bloqueos del mes
    bloqueos = {
        b.fecha: b.motivo for b in
        BloqueoCalendario.objects.filter(
            fecha__range=(primer_dia, ultimo_dia), activo=True,
        )
    }

    # Pre-cargar reservas activas del mes (para contar slots libres)
    reservas_por_fecha: dict = {}
    for fecha_, hora in Reserva.objects.filter(
        fecha__range=(primer_dia, ultimo_dia),
        estado__in=[
            Reserva.Estado.PENDIENTE_EMAIL,
            Reserva.Estado.CONFIRMADA_EMAIL,
            Reserva.Estado.CONFIRMADA_LLAMADA,
        ],
    ).values_list('fecha', 'hora_inicio'):
        reservas_por_fecha.setdefault(fecha_, set()).add(hora)

    total_slots_dia = len(_todos_los_slots())

    cal = _cal.Calendar(firstweekday=0)  # 0 = lunes
    semanas = []
    for week in cal.monthdatescalendar(year, month):
        fila = []
        for d in week:
            es_otro_mes = d.month != month
            es_pasado = d < hoy
            laboral_semana = d.weekday() in DIAS_LABORALES
            bloqueo_motivo = bloqueos.get(d)
            if not laboral_semana:
                es_laboral, motivo, libres = False, 'Cerrado', 0
            elif bloqueo_motivo:
                es_laboral, motivo, libres = False, bloqueo_motivo, 0
            elif es_pasado:
                es_laboral, motivo, libres = False, 'Pasado', 0
            else:
                es_laboral = True
                motivo = ''
                ocupadas = len(reservas_por_fecha.get(d, set()))
                libres = max(0, total_slots_dia - ocupadas)
            fila.append({
                'fecha':        d,
                'dia':          d.day,
                'es_laboral':   es_laboral,
                'motivo':       motivo,
                'slots_libres': libres,
                'es_otro_mes':  es_otro_mes,
                'es_hoy':       d == hoy,
                'es_pasado':    es_pasado,
            })
        semanas.append(fila)

    # Navegación
    prev = (primer_dia - timedelta(days=1)).replace(day=1)
    nxt  = (ultimo_dia + timedelta(days=1))

    return {
        'year':       year,
        'month':      month,
        'mes_nombre': primer_dia.strftime('%B'),
        'prev_year':  prev.year,
        'prev_month': prev.month,
        'next_year':  nxt.year,
        'next_month': nxt.month,
        'semanas':    semanas,
        'hoy':        hoy,
    }
