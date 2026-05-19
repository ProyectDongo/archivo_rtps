"""
Gestión de fondos del escritorio: subir / listar / eliminar.

Las imágenes se guardan en MEDIA_ROOT/escritorio_bg/ (no en static/) para
que sean escribibles en runtime sin necesidad de redeploy ni collectstatic.
El escritorio elige una al azar entre las `activa=True` por carga.

Acceso: solo admins (`es_admin=True`). No vale la pena crear un flag extra
para esto — es decisión estética central, no operativa.
"""
from __future__ import annotations

from django.contrib import messages
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from ..models import FondoEscritorio
from ._base import _audit, _usuario_actual, portal_login_required


_MAX_BYTES = 8 * 1024 * 1024  # 8 MB tope por foto


@portal_login_required
@never_cache
def fondos_list_view(request):
    """Galería de fondos disponibles + form de upload."""
    usuario = _usuario_actual(request)
    if not usuario or not usuario.es_admin:
        messages.error(request, 'Solo administradores pueden gestionar los fondos del escritorio.')
        return redirect('escritorio')

    fondos = FondoEscritorio.objects.all().select_related('subida_por')
    activos = sum(1 for f in fondos if f.activa)

    return render(request, 'correos/fondos_escritorio.html', {
        'usuario': usuario,
        'fondos':  fondos,
        'total':   fondos.count(),
        'activos': activos,
    })


@portal_login_required
@require_POST
def fondo_subir_view(request):
    """Subir una imagen nueva."""
    usuario = _usuario_actual(request)
    if not usuario or not usuario.es_admin:
        raise Http404

    archivo = request.FILES.get('imagen')
    nombre = (request.POST.get('nombre') or '').strip()[:120]

    if not archivo:
        messages.error(request, 'Adjuntá una imagen.')
        return redirect('fondos_list')

    if archivo.size > _MAX_BYTES:
        messages.error(request, f'La imagen pesa más de 8 MB. Comprimila antes de subir (squoosh.app, tinypng).')
        return redirect('fondos_list')

    ct = (archivo.content_type or '').lower()
    if not ct.startswith('image/'):
        messages.error(request, 'El archivo no es una imagen válida.')
        return redirect('fondos_list')
    if ct not in {'image/jpeg', 'image/png', 'image/webp'}:
        messages.error(request, 'Formato no soportado. Usá JPG, PNG o WEBP.')
        return redirect('fondos_list')

    fondo = FondoEscritorio.objects.create(
        nombre=nombre,
        imagen=archivo,
        activa=True,
        subida_por=usuario,
    )
    # Invalidar cache del random bg
    cache.delete('esc:bg:fondos:v1')
    _audit(request, 'fondo_escritorio_subir', 'fondo', fondo.id, nombre=fondo.nombre)
    messages.success(request, f'Fondo "{fondo.nombre or fondo.imagen.name}" subido.')
    return redirect('fondos_list')


@portal_login_required
@require_POST
def fondo_toggle_view(request, fondo_id: int):
    """Activar / desactivar un fondo (no lo borra, solo lo saca de la rotación)."""
    usuario = _usuario_actual(request)
    if not usuario or not usuario.es_admin:
        raise Http404
    fondo = get_object_or_404(FondoEscritorio, id=fondo_id)
    fondo.activa = not fondo.activa
    fondo.save(update_fields=['activa'])
    cache.delete('esc:bg:fondos:v1')
    _audit(request, 'fondo_escritorio_toggle', 'fondo', fondo.id, activa=fondo.activa)
    messages.success(request, f'Fondo "{fondo.nombre or fondo.imagen.name}" {"activado" if fondo.activa else "pausado"}.')
    return redirect('fondos_list')


@portal_login_required
@require_POST
def fondo_eliminar_view(request, fondo_id: int):
    """Borrar un fondo definitivamente (también borra el archivo del disco)."""
    usuario = _usuario_actual(request)
    if not usuario or not usuario.es_admin:
        raise Http404
    fondo = get_object_or_404(FondoEscritorio, id=fondo_id)
    nombre = fondo.nombre or fondo.imagen.name
    try:
        fondo.imagen.delete(save=False)
    except Exception:
        pass  # si el archivo ya no existe, igual borramos el modelo
    fondo.delete()
    cache.delete('esc:bg:fondos:v1')
    _audit(request, 'fondo_escritorio_eliminar', 'fondo', fondo_id, nombre=nombre)
    messages.success(request, f'Fondo "{nombre}" eliminado.')
    return redirect('fondos_list')
