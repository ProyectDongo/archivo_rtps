"""
Modelos de Taller: catálogo (servicios + repuestos), reservas, bloqueos.

Diseño:
  - ItemCatalogo unifica servicios y repuestos vía un campo `tipo`. Esto evita
    duplicar tablas y permite que una Reserva mezcle ambos en su carrito.
  - Reserva guarda solo el HASH del token público — el token plano viaja en
    el email del cliente y nunca se persiste en BD. Si filtran la BD, no pueden
    usar tokens robados.
  - BloqueoCalendario marca fechas no agendables (feriados gob.cl + manuales).
  - ReservaIntento es bitácora del form público para rate-limit + análisis.

Ver también: taller/constants.py para horario laboral, slots, etc.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import time

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


# ─── Constantes de operación ───────────────────────────────────────────────
# El taller atiende lun-vie 9:00 a 18:00 (último auto a las 18:00, no 18:30:
# tienen 30 min para diagnosticar antes de cierre). Almuerzo 13:00-14:00
# bloqueado. Slots de 30 min secuenciales (1 auto por slot, capacidad de
# recepción aunque internamente trabajen varios mecánicos en paralelo).
HORA_INICIO_TALLER = time(9, 0)
HORA_FIN_TALLER    = time(18, 30)        # último slot inicia 18:00, termina 18:30
HORA_ALMUERZO_INI  = time(13, 0)
HORA_ALMUERZO_FIN  = time(14, 0)
DIAS_LABORALES     = [0, 1, 2, 3, 4]     # weekday(): 0=lun, 4=vie
SLOT_MINUTOS       = 30


# ─── Validadores ───────────────────────────────────────────────────────────
patente_validator = RegexValidator(
    regex=r'^[A-Z0-9]{4,8}$',
    message='Patente inválida. Formato esperado: solo letras y números, 4-8 caracteres (ej: ABCD12, GHJK34).',
)

telefono_validator = RegexValidator(
    regex=r'^\+?[0-9\s\-]{8,20}$',
    message='Teléfono inválido. Usá formato internacional (+56912345678) o local (912345678).',
)


# ─── Helpers de token público ──────────────────────────────────────────────
def generar_token_publico() -> str:
    """32 bytes urlsafe = 43 chars. 256 bits de entropía: imposible adivinar."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 del token. Lo guardamos así en BD para que un dump no exponga tokens."""
    return hashlib.sha256(('reserva::' + (token or '')).encode('utf-8')).hexdigest()


# ─── Catálogo ──────────────────────────────────────────────────────────────
class ItemCatalogo(models.Model):
    """
    Item del catálogo del taller: puede ser un servicio (ej: "Cambio de aceite"
    con duración y precio referencial) o un repuesto (ej: "Pastillas de freno
    Toyota Hilux", con o sin instalación).

    El cliente arma su carrito eligiendo varios items. La duración total es la
    SUMA de duraciones, pero la reserva ocupa solo 1 slot de recepción (porque
    adentro hay varios mecánicos). El total es referencial — el precio final
    se ajusta al revisar el vehículo.
    """
    class Tipo(models.TextChoices):
        SERVICIO = 'servicio', 'Servicio'
        REPUESTO = 'repuesto', 'Repuesto'

    class Categoria(models.TextChoices):
        # ─ Servicios ─
        MANTENCION       = 'mantencion',       'Mantención preventiva'
        LUBRICACION      = 'lubricacion',      'Lubricación y filtros'
        FRENOS           = 'frenos',           'Frenos y embrague'
        SUSPENSION       = 'suspension',       'Suspensión y dirección'
        ELECTRICO        = 'electrico',        'Eléctrico y electrónico'
        MOTOR            = 'motor',            'Motor y transmisión'
        NEUMATICOS       = 'neumaticos',       'Neumáticos y alineación'
        AIRE             = 'aire',             'Aire acondicionado'
        DIAGNOSTICO      = 'diagnostico',      'Diagnóstico computacional'
        REVISION_TECNICA = 'revision_tecnica', 'Revisión técnica'
        CAMIONETAS_4X4   = 'camionetas_4x4',   'Camionetas / 4x4 / Minería'
        CARROCERIA       = 'carroceria',       'Carrocería y pintura'
        DETAILING        = 'detailing',        'Lavado y detailing'
        # ─ Repuestos ─
        REP_ACEITES      = 'rep_aceites',      'Aceites y lubricantes'
        REP_FILTROS      = 'rep_filtros',      'Filtros'
        REP_FRENOS       = 'rep_frenos',       'Frenos (repuestos)'
        REP_ELECTRICO    = 'rep_electrico',    'Eléctrico (repuestos)'
        REP_ILUMINACION  = 'rep_iluminacion',  'Iluminación'
        REP_NEUMATICOS   = 'rep_neumaticos',   'Neumáticos y llantas'
        REP_CORREAS      = 'rep_correas',      'Correas y kits distribución'
        REP_BUJIAS       = 'rep_bujias',       'Bujías y encendido'
        REP_REFRIGERACION = 'rep_refrigeracion','Refrigeración'
        REP_LIMPIEZA     = 'rep_limpieza',     'Limpiaparabrisas'
        REP_VIDRIOS      = 'rep_vidrios',      'Parabrisas y vidrios'
        REP_CAMIONETAS   = 'rep_camionetas',   'Camionetas/Off-road/Minería'
        OTROS            = 'otros',            'Otros'

    class Disponibilidad(models.TextChoices):
        EN_STOCK     = 'en_stock',     'En stock'
        BAJO_PEDIDO  = 'bajo_pedido',  'Bajo pedido (3-5 días)'
        CONSULTAR    = 'consultar',    'Consultar disponibilidad'

    class Marca(models.TextChoices):
        # Solo aplica a repuestos. En servicios queda vacío.
        OEM      = 'oem',      'OEM Original'
        PREMIUM  = 'premium',  'Premium compatible'
        GENERICO = 'generico', 'Genérico/económico'

    tipo        = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.SERVICIO)
    categoria   = models.CharField(max_length=30, choices=Categoria.choices, default=Categoria.MANTENCION)
    nombre      = models.CharField(max_length=120)
    descripcion = models.TextField(blank=True, max_length=2000)

    imagen        = models.ImageField(upload_to='catalogo/', blank=True, null=True,
                                      help_text='Foto del servicio/repuesto. Si está vacía, se usa el ícono.')
    icono_lucide  = models.CharField(max_length=40, default='wrench', blank=True,
                                     help_text='Nombre del ícono Lucide para el fallback (ej: wrench, oil-can, battery).')

    precio_referencia_clp = models.PositiveIntegerField(default=0, help_text='Precio referencial en CLP. El final se confirma al revisar el vehículo.')
    duracion_min          = models.PositiveIntegerField(default=30, help_text='Duración estimada en minutos. 0 = solo repuesto sin instalación.')

    disponibilidad  = models.CharField(max_length=20, choices=Disponibilidad.choices, default=Disponibilidad.EN_STOCK,
                                       help_text='Solo informativo — el control de stock real se lleva aparte.')
    marca_repuesto  = models.CharField(max_length=20, choices=Marca.choices, blank=True, default='',
                                       help_text='Solo aplica a repuestos. En servicios, dejar vacío.')

    destacado    = models.BooleanField(default=False, help_text='Marcar como "Popular" en el catálogo público.')
    activo       = models.BooleanField(default=True, help_text='Si está desactivado, no aparece en el catálogo público (sí en admin).')
    orden        = models.IntegerField(default=0, help_text='Menor número = aparece primero. Empate = orden alfabético.')

    creado       = models.DateTimeField(auto_now_add=True)
    actualizado  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Item del catálogo'
        verbose_name_plural = 'Catálogo'
        ordering = ['orden', 'nombre']
        indexes = [
            models.Index(fields=['activo', 'tipo', 'categoria']),
            models.Index(fields=['destacado', 'activo']),
        ]

    def __str__(self):
        return f'[{self.get_tipo_display()}] {self.nombre}'


# ─── Bloqueo de calendario (feriados / vacaciones / capacitaciones) ────────
class BloqueoCalendario(models.Model):
    """
    Días en que el taller NO atiende. Los feriados oficiales se cargan
    automáticamente desde la API gob.cl (ver taller/management/commands/
    cargar_feriados.py). El admin puede agregar/quitar manualmente para
    vacaciones, capacitaciones, etc.
    """
    class Fuente(models.TextChoices):
        API_GOB  = 'api_gob', 'API gob.cl (automático)'
        MANUAL   = 'manual',  'Manual (admin)'

    fecha   = models.DateField(unique=True, db_index=True)
    motivo  = models.CharField(max_length=200, help_text='Ej: "Día del trabajador", "Vacaciones del taller".')
    fuente  = models.CharField(max_length=10, choices=Fuente.choices, default=Fuente.MANUAL)
    activo  = models.BooleanField(default=True, help_text='Desmarcar para "abrir" excepcionalmente un día normalmente bloqueado.')
    creado  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Bloqueo de calendario'
        verbose_name_plural = 'Bloqueos de calendario'
        ordering = ['fecha']

    def __str__(self):
        return f'{self.fecha} · {self.motivo}'


# ─── Reserva ───────────────────────────────────────────────────────────────
class Reserva(models.Model):
    """
    Una cita de un cliente para llevar su auto al taller.

    Flujo de estados:
      pendiente_email → confirmada_email (cliente clickeó link)
                     → confirmada_llamada (admin confirmó por teléfono)
                     → cancelada_cliente | cancelada_taller
                     → completada (atendida) | no_show

    El campo `token_hash` guarda SHA-256 del token público que viaja en el email.
    El cliente accede a `/agendar/r/<token_plano>/` y nosotros hasheamos
    para buscar — el plano nunca se persiste.
    """
    class Estado(models.TextChoices):
        PENDIENTE_EMAIL    = 'pendiente_email',    'Pendiente de verificación por email'
        CONFIRMADA_EMAIL   = 'confirmada_email',   'Confirmada (email)'
        CONFIRMADA_LLAMADA = 'confirmada_llamada', 'Confirmada (llamada)'
        CANCELADA_CLIENTE  = 'cancelada_cliente',  'Cancelada por el cliente'
        CANCELADA_TALLER   = 'cancelada_taller',   'Cancelada por el taller'
        COMPLETADA         = 'completada',         'Completada'
        NO_SHOW            = 'no_show',            'No-show (no se presentó)'

    # ─ Token público (hasheado) ─
    token_hash = models.CharField(max_length=64, unique=True, db_index=True,
                                  help_text='SHA-256 del token público que va en el email del cliente.')

    # ─ Cliente ─
    cliente_nombre   = models.CharField(max_length=120)
    cliente_email    = models.EmailField(db_index=True)
    cliente_telefono = models.CharField(max_length=20, validators=[telefono_validator])

    # ─ Vehículo ─
    patente            = models.CharField(max_length=10, validators=[patente_validator], db_index=True)
    marca              = models.CharField(max_length=40, help_text='Ej: Toyota, Hyundai, Chevrolet.')
    modelo             = models.CharField(max_length=60, help_text='Ej: Hilux, Tucson, Spark.')
    anio               = models.PositiveIntegerField(null=True, blank=True,
                                                     validators=[MinValueValidator(1950)],
                                                     help_text='Año del vehículo (opcional).')
    motor              = models.CharField(max_length=40, blank=True, help_text='Ej: 1.6L, 2.5 Diesel (opcional).')
    kilometraje        = models.PositiveIntegerField(null=True, blank=True, help_text='Kilometraje aproximado (opcional).')
    contexto_problema  = models.TextField(blank=True, max_length=2000,
                                          help_text='Descripción libre: cómo empezó el problema, ruidos, síntomas. Ayuda al diagnóstico.')

    # ─ Cita ─
    fecha       = models.DateField(db_index=True)
    hora_inicio = models.TimeField()
    items       = models.ManyToManyField(ItemCatalogo, blank=True, related_name='reservas',
                                         help_text='Items elegidos del catálogo (servicios + repuestos).')
    duracion_estimada_min  = models.PositiveIntegerField(default=30,
                                                         help_text='Suma de duraciones — solo informativo, no bloquea otros slots.')
    total_referencial_clp  = models.PositiveIntegerField(default=0,
                                                         help_text='Suma de precios referenciales al momento de reservar.')

    # ─ Estado ─
    estado = models.CharField(max_length=25, choices=Estado.choices, default=Estado.PENDIENTE_EMAIL, db_index=True)

    # ─ Timestamps de eventos del ciclo de vida ─
    creada_en               = models.DateTimeField(auto_now_add=True, db_index=True)
    confirmada_email_en     = models.DateTimeField(null=True, blank=True)
    confirmada_llamada_en   = models.DateTimeField(null=True, blank=True)
    confirmada_llamada_por  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                                null=True, blank=True, related_name='reservas_confirmadas',
                                                help_text='Admin que marcó la confirmación por llamada.')
    confirmada_llamada_nota = models.TextField(blank=True, max_length=500,
                                               help_text='Ej: "Habló con la esposa", "No contesta, dejé mensaje".')
    cancelada_en            = models.DateTimeField(null=True, blank=True)
    cancelada_por           = models.CharField(max_length=120, blank=True,
                                               help_text='"cliente" si la canceló él, o el username del admin que canceló.')
    cancelada_motivo        = models.CharField(max_length=200, blank=True)

    # ─ Reminders enviados (timestamps; NULL = no enviado) ─
    reminder_24h_enviado_en = models.DateTimeField(null=True, blank=True, db_index=True)
    reminder_1h_enviado_en  = models.DateTimeField(null=True, blank=True, db_index=True)

    # ─ Auditoría de la creación ─
    ip_hash_creacion        = models.CharField(max_length=64, db_index=True, blank=True)
    user_agent_creacion     = models.CharField(max_length=500, blank=True)

    class Meta:
        verbose_name = 'Reserva'
        verbose_name_plural = 'Reservas'
        ordering = ['-fecha', '-hora_inicio']
        indexes = [
            models.Index(fields=['fecha', 'hora_inicio']),
            models.Index(fields=['estado', '-creada_en']),
            models.Index(fields=['cliente_email', '-creada_en']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['fecha', 'hora_inicio'],
                condition=~models.Q(estado__in=['cancelada_cliente', 'cancelada_taller', 'no_show']),
                name='unique_slot_activo',
            ),
        ]

    def __str__(self):
        return f'{self.fecha} {self.hora_inicio:%H:%M} · {self.cliente_nombre} · {self.patente}'

    @property
    def esta_activa(self) -> bool:
        return self.estado in (
            self.Estado.PENDIENTE_EMAIL,
            self.Estado.CONFIRMADA_EMAIL,
            self.Estado.CONFIRMADA_LLAMADA,
        )


# ─── Bitácora del form público (rate-limit + análisis) ─────────────────────
class ReservaIntento(models.Model):
    """
    Cada hit al endpoint público de reserva queda acá. Sirve para rate-limit
    por IP, detectar patrones de spam/bot, y debuguear cuando un cliente dice
    "no me deja agendar".
    """
    class Motivo(models.TextChoices):
        EXITO              = 'exito',              'Reserva creada'
        TURNSTILE_FAIL     = 'turnstile_fail',     'Turnstile falló'
        CAPTCHA_FAIL       = 'captcha_fail',       'Captcha (Fernet) falló'
        EMAIL_INVALIDO     = 'email_invalido',     'Email inválido'
        EMAIL_DESECHABLE   = 'email_desechable',   'Email desechable bloqueado'
        EMAIL_NO_VERIF     = 'email_no_verif',     'No verificó código de email'
        TELEFONO_INVALIDO  = 'telefono_invalido',  'Teléfono inválido'
        PATENTE_INVALIDA   = 'patente_invalida',   'Patente inválida'
        SLOT_OCUPADO       = 'slot_ocupado',       'Slot ya tomado'
        SLOT_PASADO        = 'slot_pasado',        'Slot en el pasado'
        FUERA_HORARIO      = 'fuera_horario',      'Fuera de horario laboral'
        FERIADO            = 'feriado',            'Día bloqueado (feriado/vacaciones)'
        THROTTLED          = 'throttled',          'Bloqueado por rate-limit'
        HONEYPOT           = 'honeypot',           'Honeypot lleno (bot)'
        OTRO               = 'otro',               'Otro'

    ip_hash         = models.CharField(max_length=64, db_index=True)
    user_agent      = models.CharField(max_length=500, blank=True)
    email_intentado = models.CharField(max_length=254, blank=True)
    motivo          = models.CharField(max_length=20, choices=Motivo.choices)
    exito           = models.BooleanField(default=False, db_index=True)
    creado          = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Intento de reserva'
        verbose_name_plural = 'Intentos de reserva'
        ordering = ['-creado']
        indexes = [
            models.Index(fields=['ip_hash', '-creado']),
            models.Index(fields=['exito', '-creado']),
        ]

    def __str__(self):
        return f'{self.creado:%Y-%m-%d %H:%M} {"OK" if self.exito else "FAIL"} {self.motivo}'
