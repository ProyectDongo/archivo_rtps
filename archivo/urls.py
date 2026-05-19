"""
URL raíz — Archivo RSP.

  /               → correos (landing + portal)
  /agendar/...    → taller (agendamiento público)
  /admin-*/...    → Django admin (ruta ofuscada vía ADMIN_URL_PATH)
  /admin-*/2fa/   → admin 2FA (setup, verify, recovery)
  /media/...      → media uploads (fondos escritorio, etc.)
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as static_serve

from taller import admin_views as taller_admin

from . import admin_2fa

urlpatterns = [
    # Taller admin (staff-only) — VA ANTES del admin.site.urls para que
    # Django no capture /admin-*/agenda/ dentro del admin de Django.
    path(settings.ADMIN_URL_PATH + 'agenda/', taller_admin.panel_agenda_view, name='taller_admin_agenda'),
    path(settings.ADMIN_URL_PATH + 'agenda/confirmar/<int:reserva_id>/', taller_admin.confirmar_llamada_view, name='taller_admin_confirmar'),

    # Admin 2FA — VA ANTES de admin.site.urls para que Django no lo capture
    path(settings.ADMIN_URL_PATH + '2fa/', include(admin_2fa.urlpatterns)),

    # Admin Django (ruta ofuscada)
    path(settings.ADMIN_URL_PATH, admin.site.urls),

    # Media uploads (fondos del escritorio, etc). Django serve es "no
    # recomendado para producción" en docs por performance, pero para volumen
    # bajo de assets públicos (fondos rotativos) es válido y simple. Si crece,
    # migrar a S3/B2 o reverse proxy directo a /app/data/adjuntos/.
    re_path(r'^media/(?P<path>.*)$', static_serve, {'document_root': settings.MEDIA_ROOT}),

    # Taller — agendamiento público
    path('', include('taller.urls')),

    # Portal correos + landing (incluye '/' al final para que capture todo lo demás)
    path('', include('correos.urls')),
]
