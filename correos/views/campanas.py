"""
Vistas para campañas de correos automáticos.

CRUD de:
- ListaDestinatarios + ContactoLista (listas reutilizables)
- CampanaCorreo (campaña con asunto, cuerpo HTML, programación)
- EnvioCampana (log, solo lectura)

Acciones extra:
- ?action=test  → envía a 1 email para previsualizar
- ?action=preview → renderiza el HTML con merge variables de muestra
- ?action=toggle  → pausa/reanuda
- contactos_importar_csv_view → bulk upload

Permisos: admins siempre, o usuarios con `puede_campanas=True`.
Cada usuario solo ve campañas/listas de los buzones a los que tiene acceso.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template import Context, Template, TemplateSyntaxError
from django.template.exceptions import TemplateDoesNotExist
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from ..models import (
    Buzon,
    CampanaCorreo,
    ContactoLista,
    EnvioCampana,
    ListaDestinatarios,
    UsuarioPortal,
)
from ..templatetags.correos_tags import render_firma_html, render_firma_texto, html_a_texto
from ._base import _audit, _buzon_actual, _usuario_actual, portal_login_required

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')


def _puede_campanas(usuario: UsuarioPortal | None) -> bool:
    """True si el usuario puede gestionar campañas (admin o flag explícito)."""
    return bool(usuario and (usuario.es_admin or usuario.puede_campanas))


def _campanas_visibles(usuario: UsuarioPortal):
    """Queryset de campañas que el usuario puede ver/editar."""
    qs = CampanaCorreo.objects.select_related('buzon', 'creada_por').prefetch_related('listas')
    if usuario.es_admin:
        return qs
    return qs.filter(buzon__in=usuario.buzones.all())


def _listas_visibles(usuario: UsuarioPortal):
    qs = ListaDestinatarios.objects.select_related('buzon').annotate(n_contactos=Count('contactos'))
    if usuario.es_admin:
        return qs
    return qs.filter(buzon__in=usuario.buzones.all())


def _validar_dias(raw) -> list[int]:
    """Convierte input del form (string '1,15,30' o lista) a [1,15,30]."""
    if isinstance(raw, list):
        items = raw
    else:
        items = (raw or '').replace(' ', '').split(',')
    out = []
    for x in items:
        try:
            n = int(x)
            if 1 <= n <= 31 and n not in out:
                out.append(n)
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _render_merge(template_str: str, ctx: dict) -> str:
    """
    Renderiza un string como template Django con un contexto. Si el template
    tiene errores de sintaxis, devuelve el string crudo (no rompe el envío).
    """
    try:
        return Template(template_str).render(Context(ctx))
    except TemplateSyntaxError:
        return template_str


# ────────────────────────────── Listado de campañas ──────────────────────────

@portal_login_required
@never_cache
def campanas_list_view(request):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        messages.error(request, 'No tenés permisos para gestionar campañas.')
        return redirect('escritorio')

    campanas = list(_campanas_visibles(usuario).annotate(
        n_envios_ok=Count('envios', filter=Q(envios__estado=EnvioCampana.ESTADO_OK)),
        n_envios_err=Count('envios', filter=Q(envios__estado=EnvioCampana.ESTADO_ERROR)),
    ))
    listas = list(_listas_visibles(usuario))

    # Buzones donde el usuario puede crear cosas
    if usuario.es_admin:
        buzones_creables = Buzon.objects.all().order_by('email')
    else:
        buzones_creables = usuario.buzones.all().order_by('email')

    return render(request, 'correos/campanas_list.html', {
        'usuario': usuario,
        'campanas': campanas,
        'listas': listas,
        'buzones_creables': buzones_creables,
    })


# ────────────────────────────── Campañas: CRUD ──────────────────────────────

@portal_login_required
@require_http_methods(['GET', 'POST'])
@never_cache
def campana_crear_view(request):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404

    if request.method == 'POST':
        return _campana_guardar(request, usuario, instancia=None)

    if usuario.es_admin:
        buzones = Buzon.objects.all().order_by('email')
    else:
        buzones = usuario.buzones.all().order_by('email')
    listas = _listas_visibles(usuario).filter(activa=True)

    return render(request, 'correos/campana_form.html', {
        'usuario': usuario,
        'campana': None,
        'buzones': buzones,
        'listas': listas,
    })


@portal_login_required
@require_http_methods(['GET', 'POST'])
@never_cache
def campana_editar_view(request, campana_id: int):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    campana = get_object_or_404(_campanas_visibles(usuario), id=campana_id)

    if request.method == 'POST':
        return _campana_guardar(request, usuario, instancia=campana)

    if usuario.es_admin:
        buzones = Buzon.objects.all().order_by('email')
    else:
        buzones = usuario.buzones.all().order_by('email')
    listas = _listas_visibles(usuario).filter(activa=True)

    envios_recientes = list(
        campana.envios.order_by('-enviado_en')[:50]
    )
    stats = {
        'total':  campana.envios.count(),
        'ok':     campana.envios.filter(estado=EnvioCampana.ESTADO_OK).count(),
        'error':  campana.envios.filter(estado=EnvioCampana.ESTADO_ERROR).count(),
    }

    return render(request, 'correos/campana_form.html', {
        'usuario': usuario,
        'campana': campana,
        'buzones': buzones,
        'listas': listas,
        'envios_recientes': envios_recientes,
        'stats': stats,
    })


def _campana_guardar(request, usuario: UsuarioPortal, instancia: CampanaCorreo | None):
    """Helper compartido por crear/editar. POST → guarda y redirige."""
    try:
        buzon_id = int(request.POST.get('buzon_id') or 0)
        if usuario.es_admin:
            buzon = Buzon.objects.get(id=buzon_id)
        else:
            buzon = usuario.buzones.get(id=buzon_id)
    except (ValueError, Buzon.DoesNotExist):
        messages.error(request, 'Buzón inválido o sin permisos.')
        return redirect('campanas')

    nombre       = (request.POST.get('nombre') or '').strip()[:160]
    asunto       = (request.POST.get('asunto') or '').strip()[:300]
    cuerpo_html  = request.POST.get('cuerpo_html') or ''
    emails_extra = (request.POST.get('emails_extra') or '').strip()
    dias_raw     = request.POST.get('dias_del_mes') or '[]'
    hora         = (request.POST.get('hora_envio') or '09:00').strip()
    activa       = bool(request.POST.get('activa'))

    if not nombre or not asunto or not cuerpo_html.strip():
        messages.error(request, 'Faltan campos obligatorios: nombre, asunto, cuerpo.')
        return redirect(request.path)

    try:
        dias_list = json.loads(dias_raw) if dias_raw.startswith('[') else dias_raw
    except json.JSONDecodeError:
        dias_list = dias_raw
    dias = _validar_dias(dias_list)
    if not dias:
        messages.error(request, 'Tenés que elegir al menos un día del mes (1-31).')
        return redirect(request.path)

    listas_ids = []
    for v in request.POST.getlist('listas[]') or request.POST.getlist('listas'):
        try:
            listas_ids.append(int(v))
        except (TypeError, ValueError):
            continue

    if instancia is None:
        campana = CampanaCorreo.objects.create(
            buzon=buzon, nombre=nombre, asunto=asunto, cuerpo_html=cuerpo_html,
            emails_extra=emails_extra, dias_del_mes=dias, hora_envio=hora,
            activa=activa, creada_por=usuario,
        )
        accion = 'campana_crear'
    else:
        campana = instancia
        campana.buzon = buzon
        campana.nombre = nombre
        campana.asunto = asunto
        campana.cuerpo_html = cuerpo_html
        campana.emails_extra = emails_extra
        campana.dias_del_mes = dias
        campana.hora_envio = hora
        campana.activa = activa
        campana.save()
        accion = 'campana_editar'

    if listas_ids:
        listas_visibles = _listas_visibles(usuario).filter(id__in=listas_ids)
        campana.listas.set(listas_visibles)
    else:
        campana.listas.clear()

    _audit(request, accion, 'campana', campana.id, nombre=campana.nombre, buzon_id=buzon.id)
    messages.success(request, f'Campaña "{campana.nombre}" guardada.')
    return redirect('campana_editar', campana_id=campana.id)


@portal_login_required
@require_POST
def campana_eliminar_view(request, campana_id: int):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    campana = get_object_or_404(_campanas_visibles(usuario), id=campana_id)
    nombre = campana.nombre
    campana.delete()
    _audit(request, 'campana_eliminar', 'campana', campana_id, nombre=nombre)
    messages.success(request, f'Campaña "{nombre}" eliminada.')
    return redirect('campanas')


@portal_login_required
@require_POST
def campana_toggle_view(request, campana_id: int):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    campana = get_object_or_404(_campanas_visibles(usuario), id=campana_id)
    campana.activa = not campana.activa
    campana.save(update_fields=['activa', 'modificada_en'])
    _audit(request, 'campana_toggle', 'campana', campana.id, activa=campana.activa)
    return JsonResponse({'ok': True, 'activa': campana.activa})


# ────────────────────────────── Test + Preview ──────────────────────────────

def _construir_email_destinatario(campana: CampanaCorreo, ctx_dest: dict):
    """
    Renderiza asunto + cuerpo de la campaña para un destinatario específico.
    Devuelve (asunto, html_completo, texto_plano).
    """
    asunto = _render_merge(campana.asunto, ctx_dest)
    cuerpo = _render_merge(campana.cuerpo_html, ctx_dest)

    # Firma del buzón (auto-append)
    firma_html = render_firma_html(campana.buzon) or ''
    html_completo = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'line-height:1.5;color:#222">' + cuerpo + '</div>' + firma_html
    )

    firma_txt = render_firma_texto(campana.buzon) or ''
    texto = html_a_texto(cuerpo)
    if firma_txt:
        texto = (texto + '\n\n' + firma_txt).strip()

    return asunto, html_completo, texto


@portal_login_required
@require_POST
def campana_test_view(request, campana_id: int):
    """Envía un email de prueba a la dirección indicada."""
    from archivo.email_utils import build_brand_logo, safe_send

    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    campana = get_object_or_404(_campanas_visibles(usuario), id=campana_id)

    dest = (request.POST.get('email') or '').strip().lower()
    if not EMAIL_RE.match(dest):
        return JsonResponse({'ok': False, 'error': 'Email inválido.'}, status=400)

    ctx_dest = {
        'nombre': 'Cliente Demo',
        'email': dest,
        'extra': {'empresa': 'Empresa Demo', 'ciudad': 'Concepción'},
    }
    asunto, html, texto = _construir_email_destinatario(campana, ctx_dest)

    brand_ctx, brand_inline = build_brand_logo()
    # Mandamos el HTML completo como cuerpo_html del template wrapper, o más
    # simple: usar EmailMultiAlternatives directo. safe_send espera template
    # files — para test, lo enviamos via EmailMultiAlternatives.
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    from email import encoders
    from email.mime.base import MIMEBase

    try:
        msg = EmailMultiAlternatives(
            subject='[PRUEBA] ' + asunto,
            body=texto,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[dest],
        )
        msg.attach_alternative(html, 'text/html')
        if brand_inline:
            msg.mixed_subtype = 'related'
            for nombre, contenido, mime, cid in brand_inline:
                part = MIMEBase(*mime.split('/', 1))
                part.set_payload(contenido)
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'inline', filename=nombre)
                part.add_header('Content-ID', f'<{cid}>')
                msg.attach(part)
        msg.send()
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)

    _audit(request, 'campana_test', 'campana', campana.id, email=dest)
    return JsonResponse({'ok': True, 'mensaje': f'Email de prueba enviado a {dest}.'})


@portal_login_required
@require_http_methods(['POST', 'GET'])
def campana_preview_view(request, campana_id: int):
    """Devuelve el HTML completo renderizado con datos de demo."""
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    campana = get_object_or_404(_campanas_visibles(usuario), id=campana_id)

    ctx_dest = {
        'nombre': 'Cliente Demo',
        'email': 'demo@ejemplo.com',
        'extra': {'empresa': 'Empresa Demo S.A.', 'ciudad': 'Concepción'},
    }
    asunto, html, _ = _construir_email_destinatario(campana, ctx_dest)
    return render(request, 'correos/campana_preview.html', {
        'campana': campana, 'asunto': asunto, 'html': html,
    })


# ────────────────────────────── Listas: CRUD ────────────────────────────────

@portal_login_required
@require_http_methods(['GET', 'POST'])
@never_cache
def lista_crear_view(request):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    if request.method == 'POST':
        return _lista_guardar(request, usuario, instancia=None)
    if usuario.es_admin:
        buzones = Buzon.objects.all().order_by('email')
    else:
        buzones = usuario.buzones.all().order_by('email')
    return render(request, 'correos/lista_form.html', {
        'usuario': usuario, 'lista': None, 'buzones': buzones, 'contactos': [],
    })


@portal_login_required
@require_http_methods(['GET', 'POST'])
@never_cache
def lista_editar_view(request, lista_id: int):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    lista = get_object_or_404(_listas_visibles(usuario), id=lista_id)
    if request.method == 'POST':
        return _lista_guardar(request, usuario, instancia=lista)
    if usuario.es_admin:
        buzones = Buzon.objects.all().order_by('email')
    else:
        buzones = usuario.buzones.all().order_by('email')
    contactos = list(lista.contactos.order_by('email'))
    return render(request, 'correos/lista_form.html', {
        'usuario': usuario, 'lista': lista, 'buzones': buzones, 'contactos': contactos,
    })


def _lista_guardar(request, usuario, instancia):
    try:
        buzon_id = int(request.POST.get('buzon_id') or 0)
        if usuario.es_admin:
            buzon = Buzon.objects.get(id=buzon_id)
        else:
            buzon = usuario.buzones.get(id=buzon_id)
    except (ValueError, Buzon.DoesNotExist):
        messages.error(request, 'Buzón inválido o sin permisos.')
        return redirect('campanas')

    nombre = (request.POST.get('nombre') or '').strip()[:120]
    descripcion = (request.POST.get('descripcion') or '').strip()
    activa = bool(request.POST.get('activa'))
    if not nombre:
        messages.error(request, 'Faltó el nombre de la lista.')
        return redirect(request.path)

    if instancia is None:
        try:
            lista = ListaDestinatarios.objects.create(
                buzon=buzon, nombre=nombre, descripcion=descripcion,
                activa=activa, creada_por=usuario,
            )
        except Exception:
            messages.error(request, 'Ya existe una lista con ese nombre en el buzón.')
            return redirect('campanas')
        _audit(request, 'lista_crear', 'lista', lista.id, nombre=lista.nombre)
    else:
        lista = instancia
        lista.buzon = buzon
        lista.nombre = nombre
        lista.descripcion = descripcion
        lista.activa = activa
        lista.save()
        _audit(request, 'lista_editar', 'lista', lista.id, nombre=lista.nombre)

    messages.success(request, f'Lista "{lista.nombre}" guardada.')
    return redirect('lista_editar', lista_id=lista.id)


@portal_login_required
@require_POST
def lista_eliminar_view(request, lista_id: int):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    lista = get_object_or_404(_listas_visibles(usuario), id=lista_id)
    nombre = lista.nombre
    lista.delete()
    _audit(request, 'lista_eliminar', 'lista', lista_id, nombre=nombre)
    messages.success(request, f'Lista "{nombre}" eliminada.')
    return redirect('campanas')


# ────────────────────────────── Contactos ───────────────────────────────────

@portal_login_required
@require_POST
def contacto_agregar_view(request, lista_id: int):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    lista = get_object_or_404(_listas_visibles(usuario), id=lista_id)

    email  = (request.POST.get('email') or '').strip().lower()
    nombre = (request.POST.get('nombre') or '').strip()[:120]
    extra_raw = (request.POST.get('datos_extra') or '').strip()

    if not EMAIL_RE.match(email):
        messages.error(request, 'Email inválido.')
        return redirect('lista_editar', lista_id=lista.id)

    try:
        extra = json.loads(extra_raw) if extra_raw else {}
        if not isinstance(extra, dict):
            extra = {}
    except json.JSONDecodeError:
        extra = {}

    ContactoLista.objects.update_or_create(
        lista=lista, email=email,
        defaults={'nombre': nombre, 'datos_extra': extra, 'activo': True},
    )
    messages.success(request, f'Contacto {email} agregado.')
    return redirect('lista_editar', lista_id=lista.id)


@portal_login_required
@require_POST
def contacto_eliminar_view(request, contacto_id: int):
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    contacto = get_object_or_404(
        ContactoLista.objects.select_related('lista__buzon'), id=contacto_id,
    )
    if not usuario.es_admin and not usuario.buzones.filter(id=contacto.lista.buzon_id).exists():
        raise Http404
    lista_id = contacto.lista_id
    email = contacto.email
    contacto.delete()
    messages.success(request, f'Contacto {email} eliminado.')
    return redirect('lista_editar', lista_id=lista_id)


@portal_login_required
@require_POST
def contactos_importar_csv_view(request, lista_id: int):
    """
    Import bulk de contactos desde CSV. Acepta columnas: email, nombre, empresa,
    telefono, ciudad, ... (todas opcionales menos email).
    Las columnas no estándar van a `datos_extra` para mail merge.
    """
    usuario = _usuario_actual(request)
    if not _puede_campanas(usuario):
        raise Http404
    lista = get_object_or_404(_listas_visibles(usuario), id=lista_id)

    archivo = request.FILES.get('csv')
    pegado = request.POST.get('csv_pegado') or ''
    if archivo:
        try:
            contenido = archivo.read().decode('utf-8-sig', errors='replace')
        except Exception as e:
            messages.error(request, f'No se pudo leer el CSV: {e}')
            return redirect('lista_editar', lista_id=lista.id)
    else:
        contenido = pegado

    if not contenido.strip():
        messages.error(request, 'Subí un archivo CSV o pegá el contenido.')
        return redirect('lista_editar', lista_id=lista.id)

    delim = ',' if contenido.count(',') >= contenido.count(';') else ';'
    reader = csv.DictReader(io.StringIO(contenido), delimiter=delim)
    if not reader.fieldnames:
        messages.error(request, 'CSV sin encabezados. La primera fila debe tener "email,nombre,...".')
        return redirect('lista_editar', lista_id=lista.id)

    headers = [h.strip().lower() for h in reader.fieldnames]
    if 'email' not in headers:
        messages.error(request, 'El CSV debe tener una columna "email".')
        return redirect('lista_editar', lista_id=lista.id)

    ok = err = 0
    for row in reader:
        row_norm = {k.strip().lower(): (v or '').strip() for k, v in row.items()}
        email = row_norm.get('email', '').lower()
        if not EMAIL_RE.match(email):
            err += 1
            continue
        nombre = row_norm.pop('nombre', '')[:120]
        row_norm.pop('email', None)
        extra = {k: v for k, v in row_norm.items() if v}
        try:
            ContactoLista.objects.update_or_create(
                lista=lista, email=email,
                defaults={'nombre': nombre, 'datos_extra': extra, 'activo': True},
            )
            ok += 1
        except Exception:
            err += 1

    messages.success(request, f'CSV procesado: {ok} contactos importados, {err} errores.')
    _audit(request, 'lista_import_csv', 'lista', lista.id, ok=ok, err=err)
    return redirect('lista_editar', lista_id=lista.id)
