"""
URL raíz — Archivo RSP.

  /               → correos (landing + portal)
  /agendar/...    → taller (agendamiento público)
  /admin-*/...    → Django admin (ruta ofuscada vía ADMIN_URL_PATH)
  /admin-*/2fa/   → admin 2FA (setup, verify, recovery)
"""
from django.conf import settings
from django.contrib import admin
from django.urls import path, include

from . import admin_2fa

urlpatterns = [
    # Admin Django (ruta ofuscada)
    path(settings.ADMIN_URL_PATH, admin.site.urls),

    # Admin 2FA
    path(settings.ADMIN_URL_PATH + '2fa/', include(admin_2fa.urlpatterns)),

    # Taller — agendamiento público
    path('', include('taller.urls')),

    # Portal correos + landing (incluye '/' al final para que capture todo lo demás)
    path('', include('correos.urls')),
]
