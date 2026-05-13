"""
URLs públicas del módulo taller (sin auth).

  GET  /agendar/                              catálogo + form + calendario
  GET  /agendar/disponibilidad/?fecha=...     AJAX JSON con slots del día
  POST /agendar/confirmar/                    crea reserva pendiente_email
  GET  /agendar/verificar/                    pide código de 6 dígitos
  POST /agendar/verificar/                    valida código → confirmada_email
  POST /agendar/reenviar-codigo/              reenvía un código nuevo
  GET  /agendar/r/<token>/                    ver mi reserva
  POST /agendar/r/<token>/confirmar/          confirma asistencia (botón email)
  POST /agendar/r/<token>/cancelar/           cancela mi reserva
"""
from django.urls import path

from . import views

urlpatterns = [
    path('agendar/',                           views.agendar_view,                  name='agendar'),
    path('agendar/disponibilidad/',            views.disponibilidad_view,           name='disponibilidad'),
    path('agendar/confirmar/',                 views.confirmar_reserva_view,        name='confirmar_reserva'),
    path('agendar/verificar/',                 views.verificar_email_view,          name='verificar_email'),
    path('agendar/reenviar-codigo/',           views.reenviar_codigo_view,          name='reenviar_codigo'),
    path('agendar/r/<str:token>/',             views.ver_reserva_view,              name='ver_reserva'),
    path('agendar/r/<str:token>/confirmar/',   views.confirmar_reserva_token_view,  name='confirmar_reserva_token'),
    path('agendar/r/<str:token>/cancelar/',    views.cancelar_reserva_view,         name='cancelar_reserva'),
]
