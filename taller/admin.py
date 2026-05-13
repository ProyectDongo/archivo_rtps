"""
Admin Django para los modelos de taller.

Diseño:
  - ItemCatalogo: CRUD completo con preview de imagen y bulk actions.
  - BloqueoCalendario: CRUD; los items con `fuente=api_gob` quedan editables
    por si querés ajustar el motivo, pero NO los borres a mano (los recarga
    el cron de cargar_feriados).
  - Reserva: read-mostly. Sólo edita estado/notas; no se modifican datos del
    cliente ni del vehículo (vienen de su form público — si hay error, contactalo).
  - ReservaIntento: estrictamente read-only (bitácora).
"""
from __future__ import annotations

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import BloqueoCalendario, ItemCatalogo, Reserva, ReservaIntento


# ─── ItemCatalogo ──────────────────────────────────────────────────────────
@admin.register(ItemCatalogo)
class ItemCatalogoAdmin(admin.ModelAdmin):
    list_display = (
        'preview_thumb', 'nombre', 'tipo', 'categoria',
        'precio_referencia_clp', 'duracion_min',
        'disponibilidad', 'destacado_icon', 'activo_icon', 'orden',
    )
    list_display_links = ('nombre',)
    list_filter = ('tipo', 'categoria', 'activo', 'destacado', 'disponibilidad', 'marca_repuesto')
    search_fields = ('nombre', 'descripcion')
    list_editable = ('orden',)
    readonly_fields = ('creado', 'actualizado', 'preview_grande')
    fieldsets = (
        ('Identificación', {
            'fields': ('tipo', 'categoria', 'nombre', 'descripcion'),
        }),
        ('Imagen / Ícono', {
            'fields': ('imagen', 'preview_grande', 'icono_lucide'),
            'description': 'Si subís imagen, se usa esa. Si no, fallback al ícono Lucide.',
        }),
        ('Precio y duración', {
            'fields': ('precio_referencia_clp', 'duracion_min'),
        }),
        ('Disponibilidad y marca', {
            'fields': ('disponibilidad', 'marca_repuesto'),
            'description': 'Marca solo para repuestos. La disponibilidad es informativa para el cliente.',
        }),
        ('Visibilidad', {
            'fields': ('destacado', 'activo', 'orden'),
        }),
        ('Auditoría', {
            'fields': ('creado', 'actualizado'),
            'classes': ('collapse',),
        }),
    )
    actions = ['activar_items', 'desactivar_items', 'marcar_destacado', 'quitar_destacado']

    @admin.display(description='', ordering='nombre')
    def preview_thumb(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="width:48px;height:48px;object-fit:cover;border-radius:8px;border:1px solid #ddd" />',
                obj.imagen.url,
            )
        return format_html('<span style="font-size:24px;opacity:.4">🛠️</span>')

    @admin.display(description='Preview de la imagen actual')
    def preview_grande(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="max-width:300px;max-height:300px;border-radius:8px;border:1px solid #ddd" />',
                obj.imagen.url,
            )
        return format_html('<em style="color:#888">Sin imagen — se mostrará el ícono "{}"</em>', obj.icono_lucide or 'wrench')

    @admin.display(description='⭐', ordering='destacado', boolean=True)
    def destacado_icon(self, obj):
        return obj.destacado

    @admin.display(description='✓', ordering='activo', boolean=True)
    def activo_icon(self, obj):
        return obj.activo

    @admin.action(description='Activar items seleccionados')
    def activar_items(self, request, queryset):
        n = queryset.update(activo=True)
        self.message_user(request, f'{n} item(s) activado(s).', messages.SUCCESS)

    @admin.action(description='Desactivar items seleccionados')
    def desactivar_items(self, request, queryset):
        n = queryset.update(activo=False)
        self.message_user(request, f'{n} item(s) desactivado(s).', messages.WARNING)

    @admin.action(description='Marcar como destacado (Popular)')
    def marcar_destacado(self, request, queryset):
        n = queryset.update(destacado=True)
        self.message_user(request, f'{n} item(s) marcado(s) como destacado.', messages.SUCCESS)

    @admin.action(description='Quitar destacado')
    def quitar_destacado(self, request, queryset):
        n = queryset.update(destacado=False)
        self.message_user(request, f'{n} item(s) sin destacar.', messages.SUCCESS)


# ─── BloqueoCalendario ─────────────────────────────────────────────────────
@admin.register(BloqueoCalendario)
class BloqueoCalendarioAdmin(admin.ModelAdmin):
    list_display  = ('fecha', 'motivo', 'fuente', 'activo_icon', 'creado')
    list_filter   = ('fuente', 'activo', 'fecha')
    search_fields = ('motivo',)
    date_hierarchy = 'fecha'
    readonly_fields = ('creado',)
    actions = ['activar_bloqueos', 'desactivar_bloqueos']

    @admin.display(description='Activo', ordering='activo', boolean=True)
    def activo_icon(self, obj):
        return obj.activo

    @admin.action(description='Activar bloqueos seleccionados')
    def activar_bloqueos(self, request, queryset):
        n = queryset.update(activo=True)
        self.message_user(request, f'{n} bloqueo(s) activado(s).', messages.SUCCESS)

    @admin.action(description='Desactivar (abrir excepcionalmente) bloqueos seleccionados')
    def desactivar_bloqueos(self, request, queryset):
        n = queryset.update(activo=False)
        self.message_user(request, f'{n} bloqueo(s) desactivado(s) — esos días vuelven a ser agendables.', messages.WARNING)


# ─── Reserva ───────────────────────────────────────────────────────────────
@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = (
        'fecha', 'hora_inicio',
        'cliente_nombre', 'cliente_telefono',
        'patente', 'marca_modelo',
        'estado_badge',
        'reminders_status',
        'creada_en',
    )
    list_filter   = ('estado', 'fecha', 'reminder_24h_enviado_en', 'reminder_1h_enviado_en')
    search_fields = ('cliente_nombre', 'cliente_email', 'cliente_telefono',
                     'patente', 'marca', 'modelo')
    date_hierarchy = 'fecha'
    filter_horizontal = ('items',)
    readonly_fields = (
        'token_hash',
        'creada_en',
        'confirmada_email_en',
        'reminder_24h_enviado_en', 'reminder_1h_enviado_en',
        'ip_hash_creacion', 'user_agent_creacion',
    )
    fieldsets = (
        ('Cliente', {
            'fields': ('cliente_nombre', 'cliente_email', 'cliente_telefono'),
        }),
        ('Vehículo', {
            'fields': ('patente', 'marca', 'modelo', 'anio', 'motor', 'kilometraje', 'contexto_problema'),
        }),
        ('Cita', {
            'fields': ('fecha', 'hora_inicio', 'items',
                       'duracion_estimada_min', 'total_referencial_clp'),
        }),
        ('Estado', {
            'fields': ('estado',
                       'confirmada_email_en',
                       'confirmada_llamada_en', 'confirmada_llamada_por', 'confirmada_llamada_nota',
                       'cancelada_en', 'cancelada_por', 'cancelada_motivo'),
        }),
        ('Reminders', {
            'fields': ('reminder_24h_enviado_en', 'reminder_1h_enviado_en'),
            'classes': ('collapse',),
        }),
        ('Auditoría', {
            'fields': ('token_hash', 'ip_hash_creacion', 'user_agent_creacion', 'creada_en'),
            'classes': ('collapse',),
        }),
    )
    actions = ['confirmar_por_llamada', 'cancelar_reserva', 'marcar_completada', 'marcar_no_show']

    @admin.display(description='Vehículo')
    def marca_modelo(self, obj):
        return f'{obj.marca} {obj.modelo}'

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        colores = {
            'pendiente_email':    ('#FFA000', '#FFF3E0'),
            'confirmada_email':   ('#1976D2', '#E3F2FD'),
            'confirmada_llamada': ('#388E3C', '#E8F5E9'),
            'cancelada_cliente':  ('#757575', '#F5F5F5'),
            'cancelada_taller':   ('#D32F2F', '#FFEBEE'),
            'completada':         ('#1B5E20', '#C8E6C9'),
            'no_show':            ('#C2185B', '#FCE4EC'),
        }
        fg, bg = colores.get(obj.estado, ('#000', '#EEE'))
        return format_html(
            '<span style="padding:3px 8px;border-radius:6px;background:{};color:{};font-weight:600;font-size:11px">{}</span>',
            bg, fg, obj.get_estado_display(),
        )

    @admin.display(description='Reminders')
    def reminders_status(self, obj):
        r24 = '✅' if obj.reminder_24h_enviado_en else '—'
        r1  = '✅' if obj.reminder_1h_enviado_en  else '—'
        return format_html('<span title="24h / 1h">{} {}</span>', r24, r1)

    @admin.action(description='Confirmar por llamada (con la nota actual)')
    def confirmar_por_llamada(self, request, queryset):
        ahora = timezone.now()
        n = 0
        for r in queryset:
            if not r.esta_activa:
                continue
            r.estado = Reserva.Estado.CONFIRMADA_LLAMADA
            r.confirmada_llamada_en = ahora
            r.confirmada_llamada_por = request.user
            r.save(update_fields=['estado', 'confirmada_llamada_en', 'confirmada_llamada_por'])
            n += 1
        self.message_user(request, f'{n} reserva(s) confirmada(s) por llamada por {request.user.username}.',
                          messages.SUCCESS)

    @admin.action(description='Cancelar (taller) — notifica al cliente automáticamente en Fase A2')
    def cancelar_reserva(self, request, queryset):
        ahora = timezone.now()
        n = 0
        for r in queryset:
            if not r.esta_activa:
                continue
            r.estado = Reserva.Estado.CANCELADA_TALLER
            r.cancelada_en = ahora
            r.cancelada_por = request.user.username
            r.cancelada_motivo = '(cancelada desde admin — completar motivo manual si aplica)'
            r.save(update_fields=['estado', 'cancelada_en', 'cancelada_por', 'cancelada_motivo'])
            n += 1
        self.message_user(request, f'{n} reserva(s) cancelada(s). En Fase A2 se mandará email automático al cliente.',
                          messages.WARNING)

    @admin.action(description='Marcar como completada')
    def marcar_completada(self, request, queryset):
        n = queryset.filter(estado__in=[
            Reserva.Estado.CONFIRMADA_EMAIL, Reserva.Estado.CONFIRMADA_LLAMADA, Reserva.Estado.PENDIENTE_EMAIL,
        ]).update(estado=Reserva.Estado.COMPLETADA)
        self.message_user(request, f'{n} reserva(s) marcada(s) como completadas.', messages.SUCCESS)

    @admin.action(description='Marcar como no-show (no se presentó)')
    def marcar_no_show(self, request, queryset):
        n = queryset.filter(estado__in=[
            Reserva.Estado.CONFIRMADA_EMAIL, Reserva.Estado.CONFIRMADA_LLAMADA, Reserva.Estado.PENDIENTE_EMAIL,
        ]).update(estado=Reserva.Estado.NO_SHOW)
        self.message_user(request, f'{n} reserva(s) marcada(s) como no-show.', messages.WARNING)


# ─── ReservaIntento (bitácora, read-only) ──────────────────────────────────
@admin.register(ReservaIntento)
class ReservaIntentoAdmin(admin.ModelAdmin):
    list_display    = ('creado', 'exito_icon', 'motivo', 'email_intentado', 'ip_corta')
    list_filter     = ('exito', 'motivo', 'creado')
    search_fields   = ('email_intentado', 'ip_hash')
    readonly_fields = [f.name for f in ReservaIntento._meta.fields]
    date_hierarchy  = 'creado'

    @admin.display(description='Estado', ordering='exito')
    def exito_icon(self, obj):
        return format_html(
            '<span style="color:{};font-weight:700">{}</span>',
            '#1B5E20' if obj.exito else '#B71C1C',
            'OK' if obj.exito else 'FAIL',
        )

    @admin.display(description='IP (hash)')
    def ip_corta(self, obj):
        return (obj.ip_hash or '')[:10] + '…'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # solo el super puede limpiar bitácora vieja
