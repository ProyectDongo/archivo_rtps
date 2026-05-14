"""
URL raíz — Archivo RSP.

  /               → correos (landing + portal)
  /agendar/...    → taller (agendamiento público)
  /admin-*/...    → Django admin (ruta ofuscada vía ADMIN_URL_PATH)
  /admin-*/2fa/   → admin 2FA (setup, verify, recovery)
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

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

    # Taller — agendamiento público
    path('', include('taller.urls')),

    # Portal correos + landing (incluye '/' al final para que capture todo lo demás)
    path('', include('correos.urls')),
]
