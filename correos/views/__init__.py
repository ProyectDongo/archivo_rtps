# Re-exporta todas las vistas para que `from . import views; views.xxx_view`
# siga funcionando sin modificar urls.py.
from .auth import (
    landing_view, healthz_view, privacidad_view,
    login_view, logout_view,
    setup_2fa_view, verify_2fa_view,
    mostrar_recovery_codes_view, confirmar_recovery_codes_view,
    descargar_recovery_pdf_view, regenerar_recovery_codes_view,
    captcha_regenerar,
    cambiar_password_view,
    # Funciones y constantes usadas en tests
    _promover_sesion,
)
from ._base import (
    REMEMBER_ME_AGE_DAYS, RE_2FA_AFTER_DAYS,
    PRE_2FA_TTL, LOCKOUT_THRESHOLD, LOCKOUT_DURACION_MIN,
    _usuario_actual, _buzon_actual, _get_ip, _audit,
    portal_login_required,
)
from .inbox import (
    inbox_view, papelera_correos_view, detalle_view, cambiar_buzon_view,
    adjunto_view, adjunto_por_cid_view,
    correo_preview_view, correo_prefill_view,
    toggle_destacado_view, toggle_leido_view,
    correo_eliminar_view, correo_restaurar_view, correo_eliminar_permanente_view,
    vaciar_papelera_view, correo_bulk_eliminar_view,
    snooze_correo_view, unsnooze_correo_view,
    actualizar_notas_view, asignar_etiqueta_view, crear_etiqueta_view,
    bulk_acciones_view,
)
from .compose import (
    contactos_view,
    reenviar_correo_view, responder_correo_view,
    firma_view,
    borradores_view, borrador_detalle_view,
    borrador_adjunto_upload_view, borrador_adjunto_delete_view,
    borrador_enviar_view,
    compose_view,
    _parse_destinatarios,
)
from .escritorio import escritorio_view
from .archivos import (
    archivos_list_view, archivos_upload_view,
    archivo_descargar_view, archivo_borrar_view,
    archivo_subir_version_view, archivo_compartir_view, archivo_descompartir_view,
    contratos_list_view,
    papelera_list_view, archivo_restaurar_view, archivo_borrar_permanente_view,
    correo_vincular_archivo_view, correo_desvincular_archivo_view,
)
from .campanas import (
    campanas_list_view,
    campana_crear_view, campana_editar_view, campana_eliminar_view, campana_toggle_view,
    campana_test_view, campana_preview_view,
    campana_ejecutar_view, campana_envios_json_view,
    lista_crear_view, lista_editar_view, lista_eliminar_view,
    contacto_agregar_view, contacto_eliminar_view,
    contactos_importar_csv_view,
)
from .taller_admin import (
    taller_items_list_view, taller_item_form_view,
    taller_item_eliminar_view, taller_item_toggle_view,
    taller_agenda_view,
    taller_reserva_detalle_view, taller_reserva_confirmar_view,
    taller_reserva_cancelar_view, taller_reserva_completar_view,
)
