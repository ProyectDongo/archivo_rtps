from ._base import (
    portal_login_required, _usuario_actual,
    logger,
)
import random
from pathlib import Path
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.db.models import Avg, Count, Exists, F, OuterRef, Q
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncDate, TruncMonth
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.cache import never_cache
from datetime import timedelta

from ..models import (
    Adjunto, Archivo, Buzon, CategoriaTema, Correo, CorreoEnviado,
    CorreoLeido, CorreoSnooze, UsuarioPortal, hash_ip,
)
from ..throttle import throttle_user

ESCRITORIO_CACHE_TTL = 30 * 60
ESCRITORIO_CHART_DIAS = 14

# Imágenes de fondo del escritorio.
# Fuente 1 (preferida): modelo FondoEscritorio (subidas desde la UI a media/).
# Fuente 2 (fallback): static/img/brutalist/*.{jpg,png,webp} (semilla manual).
# Si las dos están vacías, el escritorio cae al fondo CSS de hormigón.
_BRUTALIST_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}
_BRUTALIST_CACHE_KEY = 'esc:bg:fondos:v1'
_BRUTALIST_CACHE_TTL = 5 * 60  # 5 min — re-escanea si el usuario sube fotos


def _brutalist_bg_random() -> str | None:
    """
    Devuelve la URL de una imagen aleatoria para el fondo del escritorio.

    Prioridad:
      1. Modelo FondoEscritorio (activa=True) — la fuente "oficial",
         gestionable desde /intranet/ajustes/fondos/.
      2. Archivos en static/img/brutalist/ — fallback para deploys
         que tengan semilla manual sin haber usado todavía la UI.
      3. None — el escritorio usa el gradiente CSS de hormigón.
    """
    from ..models import FondoEscritorio  # lazy import: evita ciclo

    cache_key = _BRUTALIST_CACHE_KEY
    urls = cache.get(cache_key)
    if urls is None:
        urls = []
        # Fuente 1: DB (preferida)
        try:
            for fondo in FondoEscritorio.objects.filter(activa=True).only('imagen'):
                try:
                    urls.append(fondo.imagen.url)
                except Exception:
                    continue
        except Exception:
            pass  # tabla todavía no migrada en algún ambiente
        # Fuente 2: filesystem (fallback)
        if not urls:
            for base in (
                Path(settings.BASE_DIR) / 'static' / 'img' / 'brutalist',
                Path(settings.BASE_DIR) / 'staticfiles' / 'img' / 'brutalist',
            ):
                if not base.is_dir():
                    continue
                for f in base.iterdir():
                    if f.is_file() and f.suffix.lower() in _BRUTALIST_EXTS:
                        urls.append(static(f'img/brutalist/{f.name}'))
                if urls:
                    break
        urls = sorted(set(urls))
        cache.set(cache_key, urls, _BRUTALIST_CACHE_TTL)
    if not urls:
        return None
    return random.choice(urls)
ESCRITORIO_TEMAS_VENTANA_DIAS = 180


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
    Top N remitentes que NO son @rtriosanpedro.cl. Útil para ver quién
    desde afuera te escribe más (clientes recurrentes, bancos, etc).
    """
    cache_key = f'esc:remits:{usuario.id}:{top}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    dominio_propio = '@rtriosanpedro.cl'
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
        # Fondo brutalista aleatorio (None si no hay imágenes)
        'bg_image_url':      _brutalist_bg_random(),
    }
    return render(request, 'correos/escritorio.html', ctx)


