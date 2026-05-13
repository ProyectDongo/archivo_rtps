"""
Admin de Django personalizado.

Tres niveles de modelos:
  - UsuarioPortal: editable (CRUD) — controla quién puede entrar al portal.
  - Buzon: editable (puedes renombrar, marcar inactivo).
  - Correo, Adjunto: read-only mostly (vienen del importador, no se editan a mano).
  - IntentoLogin: read-only puro (bitácora de auditoría).

Notas de seguridad:
  - El form de UsuarioPortal NO muestra el hash. Se setea via campo password_nuevo,
    que pasa por AUTH_PASSWORD_VALIDATORS.
  - Eliminar usuarios en admin → revoca su acceso. No borra sus IntentoLogin.
"""
import logging

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from .models import AdminTOTP, Adjunto, Archivo, Buzon, BuzonGmailLabel, CategoriaTema, Correo, Etiqueta, EventoAuditoria, IntentoLogin, ReenvioCorreo, UserDesktopPrefs, UsuarioPortal

logger = logging.getLogger('correos.admin')


# ─── UsuarioPortal ─────────────────────────────────────────────────────────
class UsuarioPortalForm(forms.ModelForm):
    """
    Form custom: en vez de mostrar el hash, expone un campo "password_nuevo"
    que se valida con AUTH_PASSWORD_VALIDATORS y se hashea al guardar.
    """
    password_nuevo = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Mínimo 10 caracteres. Dejar vacío para no cambiar la contraseña actual.',
    )
    password_confirmar = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Repite la contraseña.',
    )

    class Meta:
        model = UsuarioPortal
        fields = ('email', 'es_admin', 'activo', 'buzones')

    def clean(self):
        cleaned = super().clean()
        pwd  = cleaned.get('password_nuevo') or ''
        pwd2 = cleaned.get('password_confirmar') or ''

        es_nuevo = self.instance.pk is None

        if pwd or pwd2:
            if pwd != pwd2:
                raise ValidationError({'password_confirmar': 'Las contraseñas no coinciden.'})
            try:
                validate_password(pwd, user=self.instance)
            except ValidationError as e:
                raise ValidationError({'password_nuevo': e.messages})
        elif es_nuevo:
            raise ValidationError({'password_nuevo': 'Define una contraseña para el usuario nuevo.'})

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        pwd = self.cleaned_data.get('password_nuevo')
        if pwd:
            instance.set_password(pwd)
        if commit:
            instance.save()
        return instance


@admin.register(UsuarioPortal)
class UsuarioPortalAdmin(admin.ModelAdmin):
    form = UsuarioPortalForm
    list_display  = ('email', 'es_admin', 'activo', 'estado_2fa',
                     'cantidad_buzones', 'ultimo_login', 'creado')
    list_filter   = ('es_admin', 'activo', 'totp_activo', 'buzones')
    search_fields = ('email',)
    readonly_fields = ('creado', 'ultimo_login', 'totp_activo',
                       'recovery_codes_restantes')
    filter_horizontal = ('buzones',)    # widget de doble lista
    fieldsets = (
        (None, {
            'fields': ('email', 'activo', 'es_admin'),
        }),
        ('Contraseña', {
            'fields': ('password_nuevo', 'password_confirmar'),
            'description': 'Debe tener al menos 10 caracteres y no parecerse al email.',
        }),
        ('Autenticación en dos pasos (2FA)', {
            'fields': ('totp_activo', 'recovery_codes_restantes'),
            'description': 'Para resetear 2FA del usuario, usá la acción del listado. '
                           'Tras resetear, el usuario configurará 2FA en su próximo login.',
        }),
        ('Acceso a buzones', {
            'fields': ('buzones',),
            'description': 'Si "Es admin" está marcado, ve TODOS los buzones (esta lista se ignora).',
        }),
        ('Historial', {
            'fields': ('creado', 'ultimo_login'),
        }),
    )

    @admin.display(description='# buzones', ordering='buzones')
    def cantidad_buzones(self, obj):
        if obj.es_admin:
            return format_html('<strong>todos</strong>')
        return obj.buzones.count()

    @admin.display(description='2FA', ordering='totp_activo')
    def estado_2fa(self, obj):
        if obj.totp_activo:
            return format_html('<span style="color:#1b5e20;font-weight:700">✓ activo</span>')
        return format_html('<span style="color:#b71c1c">✗ sin configurar</span>')

    @admin.display(description='Recovery codes restantes')
    def recovery_codes_restantes(self, obj):
        return len(obj.recovery_codes_hash or [])

    actions = ['desactivar_usuarios', 'activar_usuarios', 'resetear_2fa']

    @admin.action(description='Desactivar usuarios seleccionados')
    def desactivar_usuarios(self, request, queryset):
        n = queryset.update(activo=False)
        self.message_user(request, f'{n} usuario(s) desactivado(s).', messages.WARNING)

    @admin.action(description='Activar usuarios seleccionados')
    def activar_usuarios(self, request, queryset):
        n = queryset.update(activo=True)
        self.message_user(request, f'{n} usuario(s) activado(s).', messages.SUCCESS)

    @admin.action(description='Resetear 2FA (forzar reconfiguración)')
    def resetear_2fa(self, request, queryset):
        from .models import IntentoLogin, hash_ip
        n = queryset.update(
            totp_secret='',
            totp_activo=False,
            recovery_codes_hash=[],
            totp_ultimo_codigo='',
        )
        # Auditoría
        ip_h = hash_ip(request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                       or request.META.get('REMOTE_ADDR', ''))
        for u in queryset:
            try:
                IntentoLogin.objects.create(
                    ip_hash=ip_h,
                    user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:500],
                    email_intentado=u.email[:254],
                    motivo='totp_reset',
                    exito=False,
                )
            except Exception:
                logger.exception('No se pudo registrar IntentoLogin de totp_reset para %r', u.email)
        self.message_user(
            request,
            f'2FA reseteado para {n} usuario(s). En su próximo login lo configurarán de cero.',
            messages.WARNING,
        )


# ─── Buzon ─────────────────────────────────────────────────────────────────
@admin.register(Buzon)
class BuzonAdmin(admin.ModelAdmin):
    list_display  = ('email', 'nombre', 'total_correos', 'importado_en')
    search_fields = ('email', 'nombre')
    readonly_fields = ('importado_en',)


# ─── Etiqueta ──────────────────────────────────────────────────────────────
@admin.register(Etiqueta)
class EtiquetaAdmin(admin.ModelAdmin):
    list_display  = ('nombre', 'buzon', 'color_swatch', 'cantidad_correos', 'creado')
    list_filter   = ('buzon', 'color')
    search_fields = ('nombre',)
    readonly_fields = ('creado',)

    @admin.display(description='Color')
    def color_swatch(self, obj):
        return format_html(
            '<span style="display:inline-block;width:18px;height:18px;'
            'border-radius:50%;background:{};border:1px solid rgba(0,0,0,.1)" '
            'title="{}"></span> {}',
            obj.color, obj.color, obj.color,
        )

    @admin.display(description='Correos etiquetados')
    def cantidad_correos(self, obj):
        return obj.correos.count()


# ─── Correo (read-only para datos importados, pero destacado/notas/etiquetas editables) ──
@admin.register(Correo)
class CorreoAdmin(admin.ModelAdmin):
    list_display    = ('asunto_corto', 'remitente_corto', 'buzon', 'tipo_carpeta',
                       'fecha', 'tiene_adjunto', 'destacado', 'cantidad_etiquetas')
    list_filter     = ('buzon', 'tipo_carpeta', 'tiene_adjunto', 'destacado', 'etiquetas')
    search_fields   = ('asunto', 'remitente', 'destinatario', 'cuerpo_texto', 'notas')
    date_hierarchy  = 'fecha'
    filter_horizontal = ('etiquetas',)
    readonly_fields = ('buzon', 'mensaje_id', 'remitente', 'destinatario',
                       'asunto', 'fecha', 'cuerpo_texto', 'tiene_adjunto')
    fieldsets = (
        ('Datos del correo (importados, no editables)', {
            'fields': ('buzon', 'mensaje_id', 'remitente', 'destinatario',
                       'asunto', 'fecha', 'cuerpo_texto', 'tiene_adjunto'),
        }),
        ('Clasificación', {
            'fields': ('tipo_carpeta',),
            'description': 'Bandeja a la que pertenece (Inbox/Enviados/Otros). Si está mal, corregilo acá.',
        }),
        ('Organización', {
            'fields': ('destacado', 'etiquetas', 'notas'),
            'description': 'Estos campos sí se pueden editar — son del archivo, no del correo original.',
        }),
    )

    @admin.display(description='# etiquetas')
    def cantidad_etiquetas(self, obj):
        return obj.etiquetas.count()

    def asunto_corto(self, obj):
        return (obj.asunto or '(sin asunto)')[:60]
    asunto_corto.short_description = 'Asunto'

    def remitente_corto(self, obj):
        return obj.remitente_nombre[:40]
    remitente_corto.short_description = 'Remitente'

    def has_add_permission(self, request):
        return False    # se importan, no se crean a mano


# ─── Adjunto (read-only) ───────────────────────────────────────────────────
@admin.register(Adjunto)
class AdjuntoAdmin(admin.ModelAdmin):
    list_display    = ('nombre_original', 'mime_type', 'tamano_legible', 'correo_link', 'creado')
    list_filter     = ('mime_type',)
    search_fields   = ('nombre_original', 'correo__asunto')
    readonly_fields = ('correo', 'nombre_original', 'mime_type', 'tamano_bytes',
                       'archivo', 'creado')

    def correo_link(self, obj):
        return format_html('<a href="../correo/{}/change/">{}</a>',
                           obj.correo_id, (obj.correo.asunto or '(sin asunto)')[:50])
    correo_link.short_description = 'Correo'

    def has_add_permission(self, request):
        return False


# ─── IntentoLogin (auditoría, solo lectura) ────────────────────────────────
@admin.register(IntentoLogin)
class IntentoLoginAdmin(admin.ModelAdmin):
    list_display  = ('creado', 'exito_icon', 'motivo', 'email_intentado',
                     'ip_corta', 'tiempo_ms', 'honeypot_lleno', 'captcha_categoria')
    list_filter   = ('exito', 'motivo', 'creado')
    search_fields = ('email_intentado', 'ip_hash')
    readonly_fields = [f.name for f in IntentoLogin._meta.fields]
    date_hierarchy = 'creado'

    def exito_icon(self, obj):
        return format_html('<span style="color:{};font-weight:700">{}</span>',
                           '#1b5e20' if obj.exito else '#b71c1c',
                           'OK' if obj.exito else 'FAIL')
    exito_icon.short_description = 'Estado'

    def ip_corta(self, obj):
        return (obj.ip_hash or '')[:10] + '…'
    ip_corta.short_description = 'IP (hash)'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Permitir borrado en bloque para limpiar bitácora vieja
        return request.user.is_superuser


# ─── Reenvíos (auditoría, read-only para no-supers) ────────────────────────
@admin.register(ReenvioCorreo)
class ReenvioCorreoAdmin(admin.ModelAdmin):
    list_display    = ('enviado_en', 'exito_icon', 'usuario', 'destinatarios_corto',
                       'correo_link', 'tiene_nota')
    list_filter     = ('exito', 'enviado_en', 'usuario')
    search_fields   = ('destinatarios', 'mensaje_extra', 'usuario__email',
                       'correo__asunto')
    readonly_fields = [f.name for f in ReenvioCorreo._meta.fields]
    date_hierarchy  = 'enviado_en'

    @admin.display(description='Estado', ordering='exito')
    def exito_icon(self, obj):
        return format_html(
            '<span style="color:{};font-weight:700">{}</span>',
            '#1B5E20' if obj.exito else '#B71C1C',
            'OK' if obj.exito else 'FAIL',
        )

    @admin.display(description='Destinatarios')
    def destinatarios_corto(self, obj):
        return (obj.destinatarios or '')[:80]

    @admin.display(description='Correo original')
    def correo_link(self, obj):
        return format_html('<a href="../correo/{}/change/">{}</a>',
                           obj.correo_id, (obj.correo.asunto or '(sin asunto)')[:50])

    @admin.display(description='Nota', boolean=True)
    def tiene_nota(self, obj):
        return bool(obj.mensaje_extra)

    def has_add_permission(self, request):
        return False     # se generan automáticamente al reenviar desde el portal

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# ─── 2FA del admin de Django ───────────────────────────────────────────────
@admin.register(AdminTOTP)
class AdminTOTPAdmin(admin.ModelAdmin):
    list_display    = ('user', 'totp_activo', 'recovery_restantes',
                       'ultima_2fa_ok', 'creado')
    list_filter     = ('totp_activo',)
    search_fields   = ('user__username', 'user__email')
    readonly_fields = ('user', 'totp_activo', 'recovery_restantes',
                       'ultima_2fa_ok', 'creado')
    fieldsets = (
        (None, {
            'fields': ('user', 'totp_activo', 'recovery_restantes',
                       'ultima_2fa_ok', 'creado'),
        }),
    )

    @admin.display(description='Recovery codes restantes')
    def recovery_restantes(self, obj):
        return len(obj.recovery_codes_hash or [])

    actions = ['resetear_admin_2fa']

    @admin.action(description='Resetear 2FA (forzar reconfiguración)')
    def resetear_admin_2fa(self, request, queryset):
        n = queryset.update(
            totp_secret='',
            totp_activo=False,
            recovery_codes_hash=[],
            totp_ultimo_codigo='',
        )
        self.message_user(
            request,
            f'2FA reseteado para {n} admin(s). Próximo login los lleva a setup.',
            messages.WARNING,
        )

    def has_add_permission(self, request):
        # Se crean automáticamente cuando el admin entra por primera vez.
        return False


# ─── Sync Gmail por label → buzón ─────────────────────────────────────────
@admin.register(BuzonGmailLabel)
class BuzonGmailLabelAdmin(admin.ModelAdmin):
    list_display    = ('label_name', 'buzon', 'tipo_carpeta', 'activo',
                       'last_uid', 'correos_sincronizados', 'last_sync_at',
                       'estado_error')
    list_filter     = ('activo', 'tipo_carpeta', 'buzon')
    search_fields   = ('label_name', 'buzon__email')
    readonly_fields = ('last_uid', 'last_sync_at', 'correos_sincronizados',
                       'error_msg', 'creado')
    autocomplete_fields = ()    # no needed por ahora; buzón se elige del select
    fieldsets = (
        (None, {
            'fields': ('buzon', 'label_name', 'tipo_carpeta', 'activo'),
            'description': (
                'Mapea un label de Gmail (en la cuenta soporte centralizadora) '
                'al buzón del archivo. La primera corrida del cron trae toda la '
                'historia del label (last_uid=0). Después solo trae lo nuevo.'
            ),
        }),
        ('Estado del sync (read-only)', {
            'fields': ('last_uid', 'last_sync_at', 'correos_sincronizados',
                       'error_msg', 'creado'),
        }),
    )

    @admin.display(description='Estado', ordering='error_msg')
    def estado_error(self, obj):
        if obj.error_msg:
            return format_html(
                '<span style="color:#b71c1c" title="{}">⚠ error</span>',
                obj.error_msg[:120],
            )
        if obj.last_sync_at:
            return format_html('<span style="color:#1b5e20">✓ ok</span>')
        return format_html('<span style="color:#888">— sin correr</span>')

    actions = ['listar_labels_gmail', 'reset_uid', 'sincronizar_ahora']

    @admin.action(description='Listar labels disponibles en Gmail (ver mensaje)')
    def listar_labels_gmail(self, request, queryset):
        # No usa queryset; es una utility action
        from .gmail_sync import ImapError, listar_labels
        try:
            labels = listar_labels()
        except ImapError as e:
            self.message_user(request, f'IMAP error: {e}', messages.ERROR)
            return
        if not labels:
            self.message_user(request, 'Gmail no devolvió labels.', messages.WARNING)
            return
        self.message_user(
            request,
            f'Labels en Gmail ({len(labels)}): ' + ', '.join(sorted(labels)[:30])
            + ('  ... +más' if len(labels) > 30 else ''),
            messages.SUCCESS,
        )

    @admin.action(description='Resetear last_uid=0 (re-fetch completo en próxima corrida)')
    def reset_uid(self, request, queryset):
        n = queryset.update(last_uid=0)
        self.message_user(
            request,
            f'{n} sync(s) reseteados. Próxima corrida del cron trae toda la historia del label.',
            messages.WARNING,
        )

    @admin.action(description='Sincronizar AHORA (sincroniza los seleccionados)')
    def sincronizar_ahora(self, request, queryset):
        from django.core.management import call_command
        from io import StringIO
        nombres = list(queryset.values_list('label_name', flat=True))
        if not nombres:
            return
        out = StringIO()
        for label in nombres:
            try:
                call_command('sincronizar_gmail', label=label, stdout=out)
            except Exception as e:
                self.message_user(request, f'{label}: {e}', messages.ERROR)
                return
        # Resumen amigable
        lineas = [l for l in out.getvalue().splitlines() if 'nuevos' in l or 'sin novedades' in l]
        msg = ' · '.join(lineas[:5]) or 'Sync corrió. Ver detalles en BD.'
        self.message_user(request, msg, messages.SUCCESS)


# ─── Auditoría: read-only ────────────────────────────────────────────────
@admin.register(EventoAuditoria)
class EventoAuditoriaAdmin(admin.ModelAdmin):
    list_display    = ('creado', 'usuario', 'accion', 'target_tipo', 'target_id', 'ip_corta')
    list_filter     = ('accion', 'target_tipo', 'creado')
    search_fields   = ('usuario__email', 'ip_hash', 'target_tipo')
    readonly_fields = [f.name for f in EventoAuditoria._meta.fields]
    date_hierarchy  = 'creado'

    def ip_corta(self, obj):
        return (obj.ip_hash or '')[:10] + '…'
    ip_corta.short_description = 'IP (hash)'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Permitir borrado en bloque solo a superusers (limpiar bitácora vieja)
        return request.user.is_superuser


# ─── Escritorio (Fase 1) ───────────────────────────────────────────────────
@admin.register(CategoriaTema)
class CategoriaTemaAdmin(admin.ModelAdmin):
    list_display  = ('nombre', 'orden', 'color_swatch', 'activa', 'cant_keywords', 'modificado')
    list_editable = ('orden', 'activa')
    list_filter   = ('activa',)
    search_fields = ('nombre', 'keywords')
    ordering      = ('orden', 'nombre')

    fieldsets = (
        (None, {
            'fields': ('nombre', 'keywords', 'orden', 'color', 'activa'),
            'description': (
                'Estas categorías alimentan el widget "Top temas" del escritorio. '
                'Las keywords se matchean case-insensitive en el asunto + cuerpo de '
                'cada correo. Separá por coma o saltos de línea. Sin regex compleja.'
            ),
        }),
    )

    def color_swatch(self, obj):
        return format_html(
            '<span style="display:inline-block;width:14px;height:14px;'
            'border-radius:3px;background:{};vertical-align:middle;'
            'border:1px solid rgba(0,0,0,.15)"></span> <code>{}</code>',
            obj.color, obj.color,
        )
    color_swatch.short_description = 'Color'

    def cant_keywords(self, obj):
        return len(obj.keywords_lista())
    cant_keywords.short_description = '# keywords'


@admin.register(UserDesktopPrefs)
class UserDesktopPrefsAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'modificado')
    search_fields = ('usuario__email',)
    readonly_fields = ('modificado',)
    raw_id_fields = ('usuario',)


@admin.register(Archivo)
class ArchivoAdmin(admin.ModelAdmin):
    list_display  = ('nombre', 'tipo', 'perfil', 'tema', 'tamano_legible',
                     'creado', 'eliminado_en')
    list_filter   = ('tipo', 'eliminado_en', 'tema')
    search_fields = ('nombre', 'descripcion', 'tema', 'perfil__email')
    date_hierarchy = 'creado'
    raw_id_fields = ('perfil', 'creado_por', 'eliminado_por')
    readonly_fields = ('creado', 'modificado', 'tamano_bytes', 'mime_type',
                       'creado_por', 'eliminado_por')

    fieldsets = (
        ('Archivo', {
            'fields': ('archivo', 'nombre', 'tipo', 'mime_type', 'tamano_bytes'),
        }),
        ('Organización', {
            'fields': ('perfil', 'tema', 'fecha', 'descripcion'),
        }),
        ('Contrato (solo si tipo=contrato)', {
            'fields': ('contrato_partes', 'contrato_vencimiento'),
            'classes': ('collapse',),
        }),
        ('Papelera', {
            'fields': ('eliminado_en', 'eliminado_por'),
            'classes': ('collapse',),
        }),
        ('Audit', {
            'fields': ('creado_por', 'creado', 'modificado'),
            'classes': ('collapse',),
        }),
    )

    def tamano_legible(self, obj):
        return obj.tamano_legible
    tamano_legible.short_description = 'Tamaño'
