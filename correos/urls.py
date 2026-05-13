from django.urls import path

from . import views

# URLs del portal:
# - "/"           landing pública
# - "/intranet/"  acceso oculto (sin link público en el landing salvo el candadito del header)
# - "/intranet/captcha/"  AJAX para regenerar el captcha
# - "/intranet/salir/"    logout (solo POST)
# - "/intranet/bandeja/"          bandeja del usuario logueado
# - "/intranet/correo/<id>/"      detalle de un correo
urlpatterns = [
    path('healthz',                          views.healthz_view,      name='healthz'),
    path('',                                 views.landing_view,      name='landing'),
    path('privacidad/',                      views.privacidad_view,   name='privacidad'),
    path('intranet/',                        views.login_view,        name='login'),
    path('intranet/captcha/',                views.captcha_regenerar,    name='captcha_regenerar'),
    path('intranet/cambiar-password/',       views.cambiar_password_view, name='cambiar_password'),
    path('intranet/2fa/setup/',              views.setup_2fa_view,        name='setup_2fa'),
    path('intranet/2fa/verify/',             views.verify_2fa_view,       name='verify_2fa'),
    path('intranet/2fa/codigos/',            views.mostrar_recovery_codes_view, name='mostrar_recovery_codes'),
    path('intranet/2fa/codigos/confirmar/',  views.confirmar_recovery_codes_view, name='confirmar_recovery_codes'),
    path('intranet/2fa/codigos/pdf/',        views.descargar_recovery_pdf_view, name='descargar_recovery_pdf'),
    path('intranet/2fa/regenerar/',          views.regenerar_recovery_codes_view, name='regenerar_recovery_codes'),
    path('intranet/buzon/cambiar/',          views.cambiar_buzon_view,    name='cambiar_buzon'),
    path('intranet/salir/',                  views.logout_view,       name='logout'),
    path('intranet/escritorio/',             views.escritorio_view,   name='escritorio'),
    path('intranet/bandeja/',                views.inbox_view,        name='inbox'),
    path('intranet/correo/<int:correo_id>/preview/',     views.correo_preview_view,   name='correo_preview'),
    path('intranet/correo/<int:correo_id>/prefill/',     views.correo_prefill_view,   name='correo_prefill'),
    path('intranet/correo/<int:correo_id>/destacar/',    views.toggle_destacado_view, name='toggle_destacado'),
    path('intranet/correo/<int:correo_id>/leido/',       views.toggle_leido_view,     name='toggle_leido'),
    path('intranet/correo/<int:correo_id>/snooze/',      views.snooze_correo_view,    name='snooze_correo'),
    path('intranet/correo/<int:correo_id>/unsnooze/',    views.unsnooze_correo_view,  name='unsnooze_correo'),
    path('intranet/correo/<int:correo_id>/notas/',       views.actualizar_notas_view, name='actualizar_notas'),
    path('intranet/correo/<int:correo_id>/etiqueta/',    views.asignar_etiqueta_view, name='asignar_etiqueta'),
    path('intranet/correo/<int:correo_id>/reenviar/',    views.reenviar_correo_view,  name='reenviar_correo'),
    path('intranet/correo/<int:correo_id>/responder/',   views.responder_correo_view, name='responder_correo'),
    path('intranet/correo/<int:correo_id>/cid/<str:content_id>',  views.adjunto_por_cid_view,  name='adjunto_por_cid'),
    path('intranet/correo/<int:correo_id>/',             views.detalle_view,          name='detalle'),
    path('intranet/adjunto/<int:adjunto_id>/',           views.adjunto_view,          name='adjunto'),
    path('intranet/buzon/etiqueta-nueva/',               views.crear_etiqueta_view,   name='crear_etiqueta'),
    path('intranet/buzon/firma/',                        views.firma_view,            name='firma'),
    path('intranet/correos/bulk/',                       views.bulk_acciones_view,    name='bulk_acciones'),
    path('intranet/redactar/',                           views.compose_view,          name='compose'),
    path('intranet/borradores/',                         views.borradores_view,           name='borradores'),
    path('intranet/borradores/<int:borrador_id>/',       views.borrador_detalle_view,     name='borrador_detalle'),
    path('intranet/borradores/<int:borrador_id>/enviar/', views.borrador_enviar_view,     name='borrador_enviar'),
    path('intranet/borradores/<int:borrador_id>/adjuntos/',              views.borrador_adjunto_upload_view, name='borrador_adjunto_upload'),
    path('intranet/borradores/<int:borrador_id>/adjuntos/<int:adj_id>/', views.borrador_adjunto_delete_view, name='borrador_adjunto_delete'),

    # ─── Apps Archivos / Contratos / Papelera (Fase 2) ────────────────────
    path('intranet/archivos/',                                  views.archivos_list_view,             name='archivos'),
    path('intranet/archivos/subir/',                            views.archivos_upload_view,           name='archivos_upload'),
    path('intranet/archivos/<int:archivo_id>/descargar/',       views.archivo_descargar_view,         name='archivo_descargar'),
    path('intranet/archivos/<int:archivo_id>/borrar/',          views.archivo_borrar_view,            name='archivo_borrar'),
    path('intranet/archivos/<int:archivo_id>/version/',         views.archivo_subir_version_view,     name='archivo_subir_version'),
    path('intranet/archivos/<int:archivo_id>/compartir/',       views.archivo_compartir_view,         name='archivo_compartir'),
    path('intranet/archivos/<int:archivo_id>/compartir/<int:comparticion_id>/quitar/', views.archivo_descompartir_view, name='archivo_descompartir'),
    path('intranet/correo/<int:correo_id>/vincular-archivo/',                    views.correo_vincular_archivo_view,    name='correo_vincular_archivo'),
    path('intranet/correo/<int:correo_id>/vincular-archivo/<int:vinculo_id>/quitar/', views.correo_desvincular_archivo_view, name='correo_desvincular_archivo'),
    path('intranet/contratos/',                                 views.contratos_list_view,            name='contratos'),
    path('intranet/papelera/',                                  views.papelera_list_view,             name='papelera'),
    path('intranet/papelera/<int:archivo_id>/restaurar/',       views.archivo_restaurar_view,         name='archivo_restaurar'),
    path('intranet/papelera/<int:archivo_id>/borrar-permanente/', views.archivo_borrar_permanente_view, name='archivo_borrar_permanente'),
]
