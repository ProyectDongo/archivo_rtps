from ._base import (
    portal_login_required, _audit, _usuario_actual, _buzon_actual,
    _get_ip, logger,
)
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from ..models import (
    Archivo, ArchivoComparticion, ArchivoVinculo, Buzon, Correo,
    CategoriaTema, EventoAuditoria, UsuarioPortal, hash_ip,
)
from ..throttle import throttle_user

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
