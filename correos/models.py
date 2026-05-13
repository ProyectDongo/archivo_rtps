import hashlib

from django.conf import settings
from django.db import models


def hash_ip(ip: str) -> str:
    """Hash de IP para no guardar PII en claro. Sal incluida en el código."""
    return hashlib.sha256(('pm-archivo::' + (ip or '')).encode('utf-8')).hexdigest()[:32]


class Buzon(models.Model):
    """Representa un buzón/cuenta de correo importado desde .mbox"""
    email = models.EmailField(unique=True)
    nombre = models.CharField(max_length=100, blank=True)
    total_correos = models.IntegerField(default=0)
    importado_en = models.DateTimeField(auto_now_add=True)

    # ─── Firma (auto-append en correos salientes) ──────────────────────────
    # Campos estructurados (no HTML libre) para que cualquier usuario pueda
    # editar sin saber HTML. El sistema renderiza el HTML con logo + layout
    # consistente. firma_activa=False desactiva el auto-append.
    firma_activa         = models.BooleanField(default=True,
                                               help_text='Si está desactivada, no se agrega firma al enviar.')
    firma_nombre         = models.CharField(max_length=120, blank=True, default='',
                                            help_text='Nombre que aparece en la firma. Si vacío, solo se muestra el logo y el email.')
    firma_cargo          = models.CharField(max_length=120, blank=True, default='Pietramonte Automotriz',
                                            help_text='Empresa o cargo (ej. "Pietramonte Automotriz").')
    firma_telefono       = models.CharField(max_length=40, blank=True, default='')
    firma_email_visible  = models.EmailField(blank=True, default='',
                                             help_text='Email mostrado en la firma. Si vacío, usa el email del buzón.')
    firma_web            = models.CharField(max_length=120, blank=True, default='',
                                            help_text='URL del sitio web (ej. www.pietramonte.cl). Opcional.')

    def __str__(self):
        return self.email

    class Meta:
        verbose_name = 'Buzón'
        verbose_name_plural = 'Buzones'
        ordering = ['email']


class Etiqueta(models.Model):
    """
    Tag para clasificar correos. Cada etiqueta vive dentro de un buzón:
    el buzón "aledezma" puede tener "Factura", "Urgente", etc.; el buzón
    "cobranza" tiene su propio set independiente.
    """
    PALETA = [
        ('#C80C0F', 'Rojo'),
        ('#1976D2', 'Azul'),
        ('#388E3C', 'Verde'),
        ('#F57C00', 'Naranja'),
        ('#7B1FA2', 'Morado'),
        ('#5D4037', 'Café'),
        ('#455A64', 'Grafito'),
        ('#FBC02D', 'Amarillo'),
    ]

    buzon  = models.ForeignKey(Buzon, on_delete=models.CASCADE, related_name='etiquetas')
    nombre = models.CharField(max_length=40)
    color  = models.CharField(max_length=7, default='#C80C0F', choices=PALETA)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Etiqueta'
        verbose_name_plural = 'Etiquetas'
        ordering = ['nombre']
        unique_together = [('buzon', 'nombre')]
        indexes = [models.Index(fields=['buzon', 'nombre'])]

    def __str__(self):
        return f'{self.buzon.email} · {self.nombre}'


class Correo(models.Model):
    """Un correo electrónico individual indexado desde .mbox"""

    class Carpeta(models.TextChoices):
        INBOX    = 'inbox',    'Bandeja de entrada'
        ENVIADOS = 'enviados', 'Enviados'
        OTROS    = 'otros',    'Otros / sin clasificar'

    buzon = models.ForeignKey(Buzon, on_delete=models.CASCADE, related_name='correos')

    # Tipo de carpeta dentro del buzón. Lo setea import_mbox a partir del
    # nombre del archivo .mbox (heurística + override --carpeta). Para los
    # correos viejos sin clasificar, ver clasificar_correos management cmd.
    tipo_carpeta  = models.CharField(max_length=10, choices=Carpeta.choices,
                                     default=Carpeta.OTROS, db_index=True)

    mensaje_id    = models.CharField(max_length=500, blank=True, db_index=True)
    remitente     = models.CharField(max_length=500, blank=True)
    destinatario  = models.TextField(blank=True)
    asunto        = models.CharField(max_length=1000, blank=True)
    fecha         = models.DateTimeField(null=True, blank=True, db_index=True)
    cuerpo_texto  = models.TextField(blank=True)   # texto plano para búsqueda
    cuerpo_html   = models.TextField(
        blank=True, default='',
        help_text='Cuerpo en HTML si el correo lo tenía. Vacío si era solo texto plano. '
                  'Se sanitiza con bleach al renderizar (no acá) — guardarse crudo está bien.',
    )
    tiene_adjunto = models.BooleanField(default=False)

    # Organización del archivo (compartido entre todos los usuarios del buzón)
    destacado     = models.BooleanField(default=False, db_index=True)
    notas         = models.TextField(blank=True, max_length=5000,
                                     help_text='Anotaciones internas del equipo (máx 5000 chars).')
    etiquetas     = models.ManyToManyField(Etiqueta, related_name='correos', blank=True)

    class Meta:
        verbose_name = 'Correo'
        verbose_name_plural = 'Correos'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['buzon', '-fecha']),
            models.Index(fields=['buzon', 'destacado']),
            models.Index(fields=['buzon', 'tipo_carpeta', '-fecha']),
            # Para el conteo de "con adjunto" que se hace en _stats_de y para
            # el filtro ?adjuntos=1 — antes era seq scan parcial sobre el buzón.
            models.Index(fields=['buzon', 'tiene_adjunto'],
                         name='correos_cor_buzon_a_d2f8e1_idx'),
        ]
        constraints = [
            # Anti-duplicación del sync (migración 0022). Postgres rechaza
            # un segundo Correo con el mismo Message-ID en el mismo buzón.
            # Excluye mensaje_id vacío porque correos viejos importados
            # de mbox pueden carecer del header — esos quedan dedupeados
            # solo por el cursor last_uid del sync.
            models.UniqueConstraint(
                fields=['buzon', 'mensaje_id'],
                condition=~models.Q(mensaje_id=''),
                name='unique_correo_buzon_msgid',
            ),
        ]

    def __str__(self):
        return f'{self.asunto[:60]} ({self.fecha})'

    @property
    def remitente_nombre(self):
        """Extrae solo el nombre del remitente si viene en formato 'Nombre <email>'"""
        if '<' in self.remitente:
            return self.remitente.split('<')[0].strip().strip('"')
        return self.remitente

    @property
    def remitente_email(self):
        if '<' in self.remitente:
            return self.remitente.split('<')[1].strip('>')
        return self.remitente


class UsuarioPortal(models.Model):
    """
    Cuenta de acceso al portal. Cada Gmail autorizado tiene una.
    El password se guarda hasheado con PBKDF2 (default de Django).

    Acceso a buzones:
      - Si es_admin == True: ve TODOS los buzones del sistema (la M2M se ignora).
      - Si no: ve solo los buzones listados en `buzones`.

    2FA (TOTP, RFC 6238):
      - `totp_secret` base32 — generado en setup, nunca se vuelve a mostrar.
      - `totp_activo` se marca True cuando el usuario confirma el primer código.
      - `recovery_codes_hash` lista de PBKDF2-hashes; cada code se quema al usarse.
      - `totp_ultimo_codigo` anti-replay del último código usado dentro de su ventana.
    """
    email          = models.EmailField(unique=True)
    password_hash  = models.CharField(max_length=256)
    es_admin       = models.BooleanField(
        default=False,
        help_text='Si está marcado, ve TODOS los buzones (la lista de buzones se ignora).',
    )
    activo         = models.BooleanField(default=True)
    creado         = models.DateTimeField(auto_now_add=True)
    ultimo_login   = models.DateTimeField(null=True, blank=True)

    buzones        = models.ManyToManyField(
        'Buzon',
        related_name='usuarios',
        blank=True,
        help_text='Buzones que este usuario puede consultar (ignorado para admins).',
    )

    # 2FA (TOTP)
    totp_secret           = models.CharField(max_length=64, blank=True, default='')
    totp_activo           = models.BooleanField(default=False)
    recovery_codes_hash   = models.JSONField(default=list, blank=True)
    totp_ultimo_codigo    = models.CharField(max_length=10, blank=True, default='')

    # ─── Anti brute-force per-usuario (Fase de seguridad 2026-05-11) ───────
    # El rate-limit por IP no es suficiente: un atacante con botnet rotando
    # IPs puede iterar contraseñas del mismo email sin que ninguna IP
    # supere el threshold. Por eso bloqueamos también por usuario.
    intentos_fallidos = models.PositiveSmallIntegerField(
        default=0,
        help_text='Contador de logins fallidos consecutivos. Se resetea en login OK.',
    )
    bloqueado_hasta = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text='Si está en el futuro, el usuario no puede loguear hasta esa fecha. '
                  'Se setea automáticamente tras LOCKOUT_THRESHOLD fallos consecutivos.',
    )
    ultimo_intento_fallido = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp del último intento fallido — útil para auditoría.',
    )

    class Meta:
        verbose_name = 'Usuario del portal'
        verbose_name_plural = 'Usuarios del portal'
        ordering = ['email']

    def __str__(self):
        return f'{self.email}{" [admin]" if self.es_admin else ""}{"" if self.activo else " (inactivo)"}'

    def set_password(self, raw: str):
        """Hashea y guarda el password. Llamar save() después."""
        from django.contrib.auth.hashers import make_password
        self.password_hash = make_password(raw)

    def check_password(self, raw: str) -> bool:
        from django.contrib.auth.hashers import check_password
        return check_password(raw, self.password_hash)

    def buzones_visibles(self):
        """Queryset de los buzones que este usuario puede ver."""
        if self.es_admin:
            return Buzon.objects.all().order_by('email')
        return self.buzones.all().order_by('email')

    def puede_ver(self, buzon: 'Buzon') -> bool:
        """¿Tiene acceso a ese buzón concreto?"""
        if self.es_admin:
            return True
        return self.buzones.filter(id=buzon.id).exists()

    def esta_bloqueado(self) -> bool:
        """¿Está actualmente bloqueado por brute-force lockout?"""
        from django.utils import timezone
        if not self.bloqueado_hasta:
            return False
        return self.bloqueado_hasta > timezone.now()

    def registrar_intento_fallido(self, threshold: int = 5, duracion_min: int = 30) -> bool:
        """
        Incrementa el contador de fallos. Si llega a `threshold`, bloquea por
        `duracion_min` minutos. Devuelve True si esta llamada disparó el bloqueo.
        """
        from datetime import timedelta
        from django.utils import timezone

        self.intentos_fallidos = (self.intentos_fallidos or 0) + 1
        self.ultimo_intento_fallido = timezone.now()
        recien_bloqueado = False
        if self.intentos_fallidos >= threshold:
            self.bloqueado_hasta = timezone.now() + timedelta(minutes=duracion_min)
            recien_bloqueado = True
        self.save(update_fields=[
            'intentos_fallidos', 'ultimo_intento_fallido', 'bloqueado_hasta',
        ])
        return recien_bloqueado

    def resetear_intentos(self) -> None:
        """Reset del contador tras login exitoso."""
        if self.intentos_fallidos or self.bloqueado_hasta:
            self.intentos_fallidos = 0
            self.bloqueado_hasta = None
            self.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])


class CorreoLeido(models.Model):
    """
    Marca per-usuario de "este correo lo leí". El estado de lectura es
    POR USUARIO (no compartido entre el equipo): si Anghelo abre un correo
    no debería marcarse leído para soporte.dongo.

    Existencia del registro = leído. Borrar el registro = volver a no-leído.
    """
    usuario   = models.ForeignKey('UsuarioPortal', on_delete=models.CASCADE,
                                  related_name='correos_leidos')
    correo    = models.ForeignKey('Correo', on_delete=models.CASCADE,
                                  related_name='leidos_por')
    leido_en  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Correo leído'
        verbose_name_plural = 'Correos leídos'
        unique_together = [('usuario', 'correo')]
        # Nombre explícito del índice — debe coincidir con el de la migration
        # 0010_correoleido (donde lo hardcodeé en vez de dejar que Django
        # autogenere). Sin este name=, el modelo y la migración divergen y
        # `makemigrations` propone una migración de rename eterna.
        indexes = [
            models.Index(fields=['usuario', 'correo'], name='correos_cor_usuario_e8d39e_idx'),
        ]

    def __str__(self):
        return f'{self.usuario.email} leyó #{self.correo_id}'


class BorradorCorreo(models.Model):
    """
    Borrador per-usuario de un correo en redacción. Lo crea el JS de compose
    al primer cambio (debounce 1.5s) y lo va updateando con cada modificación.
    Se borra cuando el usuario:
      - Manda el correo (al hacer "Enviar" se convierte en CorreoEnviado).
      - Descarta explícitamente.

    NO persiste adjuntos (la subida + storage temporal es complejo). Si el
    usuario adjuntó archivos y cierra sin enviar, los pierde. La key visible
    para el usuario es: nunca pierde el TEXTO.
    """
    class Modo(models.TextChoices):
        COMPOSE         = 'compose',         'Composición nueva'
        RESPONDER       = 'responder',       'Responder'
        RESPONDER_TODOS = 'responder_todos', 'Responder a todos'
        REENVIAR        = 'reenviar',        'Reenviar'

    usuario          = models.ForeignKey('UsuarioPortal', on_delete=models.CASCADE,
                                         related_name='borradores')
    buzon            = models.ForeignKey('Buzon', on_delete=models.CASCADE,
                                         related_name='borradores',
                                         help_text='Buzón desde el que se enviará el correo (define el From).')
    modo             = models.CharField(max_length=20, choices=Modo.choices, default=Modo.COMPOSE)
    correo_original  = models.ForeignKey('Correo', on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='borradores_de',
                                         help_text='Solo para responder/reenviar.')
    to               = models.TextField(blank=True, default='')
    cc               = models.TextField(blank=True, default='')
    asunto           = models.CharField(max_length=1000, blank=True, default='')
    cuerpo           = models.TextField(blank=True, default='', max_length=50000)
    creado           = models.DateTimeField(auto_now_add=True)
    actualizado      = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Borrador de correo'
        verbose_name_plural = 'Borradores de correos'
        ordering = ['-actualizado']
        indexes = [
            models.Index(fields=['usuario', '-actualizado'], name='correos_brd_usr_act_idx'),
        ]

    def __str__(self):
        return f'Borrador #{self.id} · {self.usuario.email} · {self.asunto[:60] or "(sin asunto)"}'


class BorradorAdjunto(models.Model):
    borrador = models.ForeignKey(
        BorradorCorreo, on_delete=models.CASCADE, related_name='adjuntos_borrador'
    )
    nombre_original = models.CharField(max_length=500)
    mime_type = models.CharField(max_length=200, default='application/octet-stream')
    archivo = models.FileField(upload_to='borradores/%Y/%m/')
    tamanio = models.PositiveIntegerField(default=0)
    subido = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['subido']
        verbose_name = 'Adjunto de borrador'
        verbose_name_plural = 'Adjuntos de borradores'

    def delete(self, *args, **kwargs):
        if self.archivo:
            self.archivo.delete(save=False)
        super().delete(*args, **kwargs)


class CorreoSnooze(models.Model):
    """
    Snooze (posponer) per-usuario: oculta el correo de la bandeja hasta
    `until_at`. Cuando esa fecha pasa, el correo reaparece automáticamente
    (filtramos en la query con until_at > now() — no necesitamos cron).

    Existencia = activo. Borrar = unsnooze inmediato.
    """
    usuario   = models.ForeignKey('UsuarioPortal', on_delete=models.CASCADE,
                                  related_name='correos_snoozed')
    correo    = models.ForeignKey('Correo', on_delete=models.CASCADE,
                                  related_name='snoozes')
    until_at  = models.DateTimeField(db_index=True,
                                     help_text='El correo está oculto hasta este momento.')
    creado    = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Correo pospuesto (snooze)'
        verbose_name_plural = 'Correos pospuestos'
        unique_together = [('usuario', 'correo')]
        indexes = [
            models.Index(fields=['usuario', 'until_at'], name='correos_snz_usr_until_idx'),
        ]

    def __str__(self):
        return f'{self.usuario.email} → #{self.correo_id} hasta {self.until_at:%Y-%m-%d %H:%M}'


class Adjunto(models.Model):
    """
    Archivo adjunto extraído de un correo .mbox y guardado en MEDIA_ROOT.
    Solo se sirve a usuarios logueados que tengan acceso al buzón asociado.
    """
    correo          = models.ForeignKey(Correo, on_delete=models.CASCADE, related_name='adjuntos')
    nombre_original = models.CharField(max_length=300)
    mime_type       = models.CharField(max_length=200, blank=True)
    tamano_bytes    = models.PositiveBigIntegerField(default=0)
    archivo         = models.FileField(upload_to='adjuntos/%Y/%m/')
    creado          = models.DateTimeField(auto_now_add=True)

    # Content-ID del part MIME (sin angle brackets). Solo lo traen los adjuntos
    # *inline* (imágenes embebidas en HTML). Se usa para resolver `cid:xxx`
    # en el cuerpo HTML al renderizar — sino se ve `[cid:xxx]` como texto.
    content_id      = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Content-ID (sin <>) del adjunto inline. Vacío para attachments normales.',
    )

    class Meta:
        verbose_name = 'Adjunto'
        verbose_name_plural = 'Adjuntos'
        ordering = ['nombre_original']
        indexes = [models.Index(fields=['correo'])]

    def __str__(self):
        return f'{self.nombre_original} ({self.tamano_bytes} bytes)'

    @property
    def tamano_legible(self) -> str:
        n = self.tamano_bytes
        for unidad in ['B', 'KB', 'MB', 'GB']:
            if n < 1024:
                return f'{n:.1f} {unidad}' if unidad != 'B' else f'{n} {unidad}'
            n /= 1024
        return f'{n:.1f} TB'

    @property
    def es_seguro_inline(self) -> bool:
        """¿Se puede mostrar inline en el navegador sin riesgo XSS?"""
        seguros = {
            'application/pdf',
            'image/png', 'image/jpeg', 'image/gif', 'image/webp',
            'audio/mpeg', 'audio/ogg',
            'video/mp4', 'video/webm',
        }
        return self.mime_type.lower() in seguros


class IntentoLogin(models.Model):
    """
    Bitácora de cada intento de login. Datos para:
      - Bloqueo por rate-limit (consultas por ip_hash en últimos N minutos).
      - Análisis / ML futuro (detección de patrones de bot).
    No guardamos IP en claro: solo hash con sal interna.
    """
    MOTIVOS = [
        ('exito',             'Login exitoso'),
        ('honeypot',          'Honeypot lleno'),
        ('muy_rapido',        'Tiempo sospechosamente bajo'),
        ('captcha_fail',      'Captcha incorrecto/expirado'),
        ('email_no_lista',    'Email fuera de allowlist'),
        ('email_invalido',    'Formato de email inválido'),
        ('password_invalida', 'Contraseña incorrecta'),
        ('usuario_inactivo',  'Usuario marcado inactivo'),
        ('usuario_bloqueado', 'Cuenta bloqueada por brute-force lockout'),
        ('buzon_inexist',     'Buzón no importado'),
        ('throttled',         'Bloqueado por rate-limit'),
        ('csrf',              'CSRF inválido'),
        ('pwd_ok_2fa_pend',   'Password OK, 2FA pendiente'),
        ('totp_fail',         'Código 2FA incorrecto'),
        ('totp_ok',           '2FA verificado'),
        ('recovery_used',     'Recovery code usado'),
        ('recovery_inval',    'Recovery code inválido'),
        ('totp_setup',        '2FA configurado por primera vez'),
        ('totp_reset',        '2FA reseteado por admin'),
    ]

    ip_hash         = models.CharField(max_length=64, db_index=True)
    user_agent      = models.CharField(max_length=500, blank=True)
    email_intentado = models.CharField(max_length=254, blank=True)
    captcha_categoria = models.CharField(max_length=30, blank=True)
    tiempo_ms       = models.IntegerField(default=0)
    honeypot_lleno  = models.BooleanField(default=False)
    exito           = models.BooleanField(default=False, db_index=True)
    motivo          = models.CharField(max_length=20, choices=MOTIVOS, blank=True)
    creado          = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Intento de login'
        verbose_name_plural = 'Intentos de login'
        ordering = ['-creado']
        indexes = [
            models.Index(fields=['ip_hash', '-creado']),
            models.Index(fields=['exito', '-creado']),
        ]

    def __str__(self):
        return f'{self.creado:%Y-%m-%d %H:%M} {"OK" if self.exito else "FAIL"} {self.motivo}'


class ReenvioCorreo(models.Model):
    """
    Bitácora de cada vez que un UsuarioPortal reenvía un correo del archivo
    a un destinatario externo.

    Pensado para:
      - Auditoría: quién reenvió qué y a quién (los Correos pueden ser
        sensibles — facturas, contratos, etc.).
      - Rate-limit: contar reenvíos de las últimas 24h por usuario y bloquear
        si supera el cupo (30 normal, 100 admin).

    El cuerpo del email reenviado se arma en el momento — no se persiste.
    Los destinatarios SÍ se guardan (texto coma-separado).
    """
    correo          = models.ForeignKey(Correo, on_delete=models.CASCADE,
                                        related_name='reenvios')
    usuario         = models.ForeignKey(UsuarioPortal, on_delete=models.SET_NULL,
                                        null=True, blank=True,
                                        related_name='reenvios_realizados',
                                        help_text='Usuario que disparó el reenvío. NULL si el usuario fue eliminado.')
    destinatarios   = models.TextField(help_text='Emails coma-separados a los que se envió.')
    mensaje_extra   = models.TextField(blank=True, max_length=2000,
                                       help_text='Nota que el usuario agregó arriba del correo original.')
    enviado_en      = models.DateTimeField(auto_now_add=True, db_index=True)
    exito           = models.BooleanField(default=False, db_index=True,
                                          help_text='True si el envío SMTP completó sin error.')
    error_msg       = models.TextField(blank=True, max_length=500,
                                       help_text='Mensaje de error si el envío falló.')
    ip_hash         = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        verbose_name = 'Reenvío de correo'
        verbose_name_plural = 'Reenvíos de correos'
        ordering = ['-enviado_en']
        indexes = [
            models.Index(fields=['usuario', '-enviado_en']),
            models.Index(fields=['exito', '-enviado_en']),
        ]

    def __str__(self):
        return f'{self.enviado_en:%Y-%m-%d %H:%M} · {self.usuario_id} → {self.destinatarios[:60]}'


class CorreoEnviado(models.Model):
    """
    Bitácora de cada respuesta o composición nueva enviada desde el portal.

    Distinto de `ReenvioCorreo` (que es solo "forward de un correo del archivo
    a externos sin que el destinatario pueda responder al hilo"), este modelo
    cubre el caso "responder al remitente / responder a todos / componer
    nuevo" donde el From es la dirección del buzón y el destinatario puede
    responder y la respuesta vuelve via sync IMAP al mismo buzón.

    El `Correo` saved-to-sent se crea aparte (con `tipo_carpeta='enviados'`)
    para que el usuario lo vea en la pestaña "Enviados". Esta tabla es para
    auditoría: incluye errores de envío, IP, etc.
    """
    class Tipo(models.TextChoices):
        RESPONDER       = 'responder',        'Responder'
        RESPONDER_TODOS = 'responder_todos',  'Responder a todos'
        COMPOSE         = 'compose',          'Composición nueva'

    buzon            = models.ForeignKey('Buzon', on_delete=models.CASCADE,
                                         related_name='correos_enviados',
                                         help_text='Buzón desde el que se envió (define el From).')
    usuario          = models.ForeignKey('UsuarioPortal', on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='enviados_realizados')
    correo_original  = models.ForeignKey('Correo', on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='respuestas',
                                         help_text='Correo al que se respondió. NULL si fue compose nuevo.')
    correo_guardado  = models.ForeignKey('Correo', on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='entrada_envio',
                                         help_text='Copia guardada en BD con tipo_carpeta=enviados.')
    tipo             = models.CharField(max_length=20, choices=Tipo.choices,
                                        default=Tipo.RESPONDER, db_index=True)
    destinatarios    = models.TextField(help_text='Emails del To, coma-separados.')
    cc               = models.TextField(blank=True, help_text='Emails del Cc, coma-separados.')
    asunto           = models.CharField(max_length=1000)
    cuerpo           = models.TextField(blank=True,
                                        help_text='Body que escribió el usuario (sin el quote del original).')
    mensaje_id       = models.CharField(max_length=500, blank=True,
                                        help_text='Message-ID que generamos para este envío.')
    in_reply_to      = models.CharField(max_length=500, blank=True,
                                        help_text='Message-ID del correo al que respondemos.')
    enviado_en       = models.DateTimeField(auto_now_add=True, db_index=True)
    exito            = models.BooleanField(default=False, db_index=True)
    error_msg        = models.TextField(blank=True, max_length=500)
    ip_hash          = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        verbose_name = 'Correo enviado desde el portal'
        verbose_name_plural = 'Correos enviados desde el portal'
        ordering = ['-enviado_en']
        # Nombres explícitos para que matcheen con la migration 0011_correoenviado
        # (donde los hardcodeé). Mismo motivo que CorreoLeido.
        indexes = [
            models.Index(fields=['usuario', '-enviado_en'], name='correos_cor_usuario_4f8d2a_idx'),
            models.Index(fields=['buzon', '-enviado_en'],   name='correos_cor_buzon_i_e1c39b_idx'),
            models.Index(fields=['exito', '-enviado_en'],   name='correos_cor_exito_3a7e4f_idx'),
        ]

    def __str__(self):
        return f'{self.enviado_en:%Y-%m-%d %H:%M} · {self.tipo} · {self.buzon_id} → {self.destinatarios[:60]}'


class BuzonGmailLabel(models.Model):
    """
    Mapea un label de Gmail (en la cuenta soporte central) → un Buzon del
    archivo. El management command `sincronizar_gmail` corre por cron, abre
    una conexión IMAP a Gmail, y por cada label activo fetchea los mensajes
    con UID > last_uid → los inserta como Correo en el buzón asociado.

    El dedup por (buzon, mensaje_id) ya está garantizado por el flow de
    import_mbox (mismo código). Si el cron corre 2 veces no duplica.

    last_uid arranca en 0 → primera corrida importa TODA la historia del
    label. Después solo entra lo nuevo (UID monotónicamente creciente).
    """
    buzon         = models.ForeignKey(Buzon, on_delete=models.CASCADE, related_name='gmail_labels')
    label_name    = models.CharField(max_length=200,
                                     help_text='Nombre EXACTO del label en Gmail. Case-sensitive. '
                                               'Para ver los disponibles, usá la action '
                                               '"Listar labels disponibles" en este admin.')
    tipo_carpeta  = models.CharField(max_length=10, choices=Correo.Carpeta.choices,
                                     default=Correo.Carpeta.INBOX,
                                     help_text='Bajo qué pestaña aparecen estos correos en el portal.')
    activo        = models.BooleanField(default=True)
    last_uid      = models.PositiveBigIntegerField(default=0,
                                                   help_text='UID del último mensaje IMAP sincronizado. '
                                                             '0 = traer toda la historia del label en la próxima corrida.')
    last_sync_at  = models.DateTimeField(null=True, blank=True)
    correos_sincronizados = models.IntegerField(default=0)
    error_msg     = models.TextField(blank=True, max_length=1000,
                                     help_text='Último error de sync (si hay).')
    creado        = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sync Gmail label → buzón'
        verbose_name_plural = 'Sync Gmail labels → buzones'
        unique_together = [('buzon', 'label_name')]
        ordering = ['buzon__email', 'label_name']

    def __str__(self):
        estado = '' if self.activo else ' (inactivo)'
        return f'{self.label_name} → {self.buzon.email}{estado}'


class EventoAuditoria(models.Model):
    """
    Bitácora minimalista de acciones sensibles per-usuario en el portal.
    Cubre lo que NO está ya logueado por modelos específicos
    (`IntentoLogin`, `ReenvioCorreo`, `CorreoEnviado`):
      - cambios de password
      - setup/regeneración de 2FA
      - acciones masivas (bulk_acciones)
      - snooze / unsnooze
      - asignar/quitar etiquetas
      - cambios de firma del buzón
      - creación/borrado de borradores

    Diseño:
      - usuario nullable (FK SET_NULL) para sobrevivir borrado de cuentas.
      - target_id genérico (int) — apunta al objeto afectado pero NO usa
        GenericForeignKey (overhead innecesario para una bitácora).
      - target_tipo es un string corto que indica de qué tabla viene
        (correo, buzon, etiqueta, borrador, etc).
      - meta JSONField para detalle libre (qué cambió, antes/después).
      - ip_hash igual que IntentoLogin (no PII).

    Retención: si crece mucho, agregar cron que purgue entradas > 1 año.
    """
    ACCIONES = [
        # Auth / 2FA
        ('login_ok',           'Login exitoso'),
        ('logout',             'Logout'),
        ('password_cambio',    'Cambio de password'),
        ('totp_setup',         '2FA configurado'),
        ('totp_reset',         '2FA reseteado'),
        ('recovery_regen',     'Recovery codes regenerados'),
        # Correos / acciones bulk
        ('bulk_leer',          'Marcar leídos en masa'),
        ('bulk_no_leer',       'Marcar no-leídos en masa'),
        ('bulk_destacar',      'Destacar en masa'),
        ('bulk_etiquetar',     'Etiquetar en masa'),
        # Snooze
        ('snooze',             'Posponer correo'),
        ('unsnooze',           'Cancelar snooze'),
        # Etiquetas
        ('etiqueta_crear',     'Etiqueta creada'),
        ('etiqueta_asignar',   'Etiqueta asignada a correo'),
        ('etiqueta_quitar',    'Etiqueta quitada de correo'),
        # Firma
        ('firma_actualizar',   'Firma del buzón actualizada'),
        # Borradores
        ('borrador_crear',     'Borrador creado'),
        ('borrador_borrar',    'Borrador descartado'),
    ]

    usuario      = models.ForeignKey('UsuarioPortal', on_delete=models.SET_NULL,
                                     null=True, blank=True,
                                     related_name='eventos_auditoria')
    accion       = models.CharField(max_length=30, choices=ACCIONES, db_index=True)
    target_tipo  = models.CharField(max_length=20, blank=True, default='',
                                    help_text='Modelo afectado (correo, buzon, etiqueta, borrador, …).')
    target_id    = models.PositiveIntegerField(null=True, blank=True,
                                               help_text='PK del objeto afectado.')
    meta         = models.JSONField(default=dict, blank=True,
                                    help_text='Detalle libre: ids, valores, etc.')
    ip_hash      = models.CharField(max_length=64, blank=True, default='', db_index=True)
    creado       = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Evento de auditoría'
        verbose_name_plural = 'Eventos de auditoría'
        ordering = ['-creado']
        indexes = [
            models.Index(fields=['usuario', '-creado'],     name='correos_evt_usr_act_idx'),
            models.Index(fields=['accion', '-creado'],      name='correos_evt_acc_act_idx'),
        ]

    def __str__(self):
        u = self.usuario.email if self.usuario else 'anon'
        return f'{self.creado:%Y-%m-%d %H:%M} {u} {self.accion} {self.target_tipo}#{self.target_id or "-"}'


class AdminTOTP(models.Model):
    """
    2FA del superuser de Django (auth.User). 1:1 con User.
    Se crea on-demand cuando el admin entra y todavía no tiene perfil.
    Mismo esquema TOTP+recovery que UsuarioPortal pero separado para no
    contaminar el modelo de Django auth.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='totp',
    )
    totp_secret           = models.CharField(max_length=64, blank=True, default='')
    totp_activo           = models.BooleanField(default=False)
    recovery_codes_hash   = models.JSONField(default=list, blank=True)
    totp_ultimo_codigo    = models.CharField(max_length=10, blank=True, default='')
    creado                = models.DateTimeField(auto_now_add=True)
    ultima_2fa_ok         = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '2FA de admin'
        verbose_name_plural = '2FA de admins'

    def __str__(self):
        estado = 'activo' if self.totp_activo else 'sin configurar'
        return f'{self.user} · {estado}'


# ─────────────────────────────────────────────────────────────────────────────
# Escritorio / Home (Fase 1 del rediseño "almacén digital")
# ─────────────────────────────────────────────────────────────────────────────

class CategoriaTema(models.Model):
    """
    Categoría para clasificar correos por "tema mencionado" (cotización,
    factura, repuesto, etc.). El widget "Top temas" del escritorio cuenta
    cuántos correos matchean cada categoría.

    Reglas keyword/regex en `keywords` (lista de strings o patrones simples
    separados por coma — sin regex compleja en MVP). El matcher busca en
    asunto + cuerpo_texto.

    Editable desde Ajustes (Fase 1.5) o admin (Fase 1).
    """
    nombre = models.CharField(max_length=80, unique=True)
    keywords = models.TextField(
        help_text='Palabras clave separadas por coma o saltos de línea. '
                  'Match case-insensitive contra asunto + cuerpo del correo.',
    )
    color = models.CharField(
        max_length=7,
        default='#C80C0F',
        help_text='Color hex del chip en el widget (ej. #C80C0F).',
    )
    orden = models.PositiveSmallIntegerField(
        default=100,
        help_text='Menor = aparece más arriba en el widget Top temas.',
    )
    activa = models.BooleanField(
        default=True,
        help_text='Si está desactivada, no aparece en el widget ni se cuenta.',
    )
    creado = models.DateTimeField(auto_now_add=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Categoría de tema'
        verbose_name_plural = 'Categorías de tema'
        ordering = ['orden', 'nombre']
        indexes = [
            models.Index(fields=['activa', 'orden'], name='correos_cat_act_ord_idx'),
        ]

    def __str__(self):
        return self.nombre

    def keywords_lista(self) -> list[str]:
        """Devuelve las keywords ya parseadas (split por coma o newline, sin vacíos)."""
        raw = (self.keywords or '').replace('\n', ',')
        return [k.strip().lower() for k in raw.split(',') if k.strip()]


class Archivo(models.Model):
    """
    Documento digital subido al portal — base para apps Archivos, Contratos,
    Papelera. Un solo modelo con campo `tipo` que diferencia.

    Organización:
      - `perfil` (FK Buzon): a quién pertenece / responsable (Claudia, Vicente,
        Compartido, etc.). Reusa la lista de Buzones para consistencia.
      - `tema`: clasificación libre (Facturación, Proveedores, Mantención…).
      - `fecha`: fecha asociada al documento (no la de upload — ej. fecha de
        factura, fecha del contrato).

    Soft-delete (Papelera): si `eliminado_en` no es null, el archivo está en
    la papelera. Los listados normales filtran por `eliminado_en__isnull=True`.
    Un cron puede purgar archivos en papelera > N días (TODO).
    """
    class Tipo(models.TextChoices):
        DOCUMENTO = 'doc',       'Documento'
        CONTRATO  = 'contrato',  'Contrato'
        FACTURA   = 'factura',   'Factura'
        IMAGEN    = 'imagen',    'Imagen'
        OTRO      = 'otro',      'Otro'

    class Visibilidad(models.TextChoices):
        PRIVADO = 'privado',   'Privado · solo yo y admins'
        PERFIL  = 'perfil',    'Por perfil · users con acceso al buzón'
        PUBLICO = 'publico',   'Público · todos los users del portal'

    nombre        = models.CharField(max_length=200,
                                     help_text='Nombre descriptivo (no el filename).')
    archivo       = models.FileField(upload_to='archivos/%Y/%m/')
    mime_type     = models.CharField(max_length=200, blank=True)
    tamano_bytes  = models.PositiveBigIntegerField(default=0)
    tipo          = models.CharField(
        max_length=15, choices=Tipo.choices, default=Tipo.DOCUMENTO, db_index=True,
        help_text='Categoría del archivo. Define en qué app aparece.',
    )

    # Organización por perfil/tema/fecha
    perfil        = models.ForeignKey(
        Buzon, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='archivos',
        help_text='Perfil/responsable. Si vacío, el archivo es "Compartido".',
    )
    tema          = models.CharField(
        max_length=80, blank=True, default='', db_index=True,
        help_text='Tema/carpeta libre (ej. Facturación, Proveedores).',
    )
    fecha         = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='Fecha del documento (no la de upload).',
    )

    # Visibilidad explícita (anti-ambigüedad del modelo viejo "perfil=None=todos")
    visibilidad   = models.CharField(
        max_length=10, choices=Visibilidad.choices, default=Visibilidad.PERFIL,
        db_index=True,
        help_text='Privado: solo yo + admins. Perfil: users con acceso al buzón. '
                  'Público: todos los users del portal.',
    )

    descripcion   = models.TextField(blank=True, default='')

    # ─── Contrato (solo aplica si tipo=='contrato') ──────────────────────
    contrato_partes      = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Partes firmantes (separadas por ;). Solo para tipo=contrato.',
    )
    contrato_vencimiento = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='Fecha de vencimiento. Solo para tipo=contrato.',
    )

    # ─── Audit ───────────────────────────────────────────────────────────
    creado        = models.DateTimeField(auto_now_add=True, db_index=True)
    modificado    = models.DateTimeField(auto_now=True)
    creado_por    = models.ForeignKey(
        'UsuarioPortal', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='archivos_subidos',
    )

    # ─── Soft-delete (Papelera) ──────────────────────────────────────────
    eliminado_en  = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text='Si no es null, el archivo está en la papelera.',
    )
    eliminado_por = models.ForeignKey(
        'UsuarioPortal', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='archivos_eliminados',
    )

    # ─── Versiones ───────────────────────────────────────────────────────
    # Cada versión es un row independiente. La "v1" tiene version_padre=NULL;
    # las siguientes apuntan al archivo raíz (NO encadenado v2→v3, todos→v1).
    # version_num arranca en 1 y se autoincrementa al subir versión nueva.
    version_padre = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='versiones', db_index=True,
        help_text='Archivo raíz si esto es una versión posterior. '
                  'NULL = es la raíz (puede tener versiones hijas).',
    )
    version_num   = models.PositiveSmallIntegerField(
        default=1,
        help_text='Número de versión (1 = original).',
    )
    version_nota  = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Qué cambió en esta versión (opcional).',
    )

    class Meta:
        verbose_name = 'Archivo'
        verbose_name_plural = 'Archivos'
        ordering = ['-creado']
        indexes = [
            models.Index(fields=['tipo', '-creado'],  name='correos_arc_tipo_idx'),
            models.Index(fields=['perfil', '-creado'], name='correos_arc_perfil_idx'),
            models.Index(fields=['eliminado_en'],     name='correos_arc_elim_idx'),
            models.Index(fields=['version_padre', '-version_num'], name='correos_arc_ver_idx'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.nombre}'

    @property
    def en_papelera(self) -> bool:
        return self.eliminado_en is not None

    @property
    def tamano_legible(self) -> str:
        n = self.tamano_bytes
        for unidad in ['B', 'KB', 'MB', 'GB']:
            if n < 1024:
                return f'{n:.1f} {unidad}' if unidad != 'B' else f'{n} {unidad}'
            n /= 1024
        return f'{n:.1f} TB'

    def soft_delete(self, usuario):
        """Mover a papelera."""
        from django.utils import timezone
        self.eliminado_en = timezone.now()
        self.eliminado_por = usuario
        self.save(update_fields=['eliminado_en', 'eliminado_por'])

    def restaurar(self):
        """Sacar de papelera."""
        self.eliminado_en = None
        self.eliminado_por = None
        self.save(update_fields=['eliminado_en', 'eliminado_por'])

    def puede_ver(self, usuario) -> bool:
        """
        ¿Este usuario puede ver este archivo?
          - Admins: TODO.
          - Uploader: siempre el suyo.
          - Compartido explícito: si hay ArchivoComparticion para este user.
          - Público: todos los users del portal.
          - Por perfil: si el usuario puede ver el buzón asignado.
          - Privado: solo uploader + admins (+ compartidos).
        """
        if not usuario:
            return False
        if usuario.es_admin:
            return True
        if self.creado_por_id and self.creado_por_id == usuario.id:
            return True
        # Compartido explícito (privado o perfil) — siempre habilita.
        if ArchivoComparticion.objects.filter(archivo=self, usuario=usuario).exists():
            return True
        if self.visibilidad == self.Visibilidad.PUBLICO:
            return True
        if self.visibilidad == self.Visibilidad.PERFIL and self.perfil:
            return usuario.puede_ver(self.perfil)
        # PRIVADO o PERFIL sin perfil asignado: solo uploader + admins
        return False

    @property
    def carpeta_segments(self) -> list[str]:
        """Devuelve los segmentos de la 'carpeta virtual' del tema.
        Ej: tema='Facturación/2026/Enero' → ['Facturación', '2026', 'Enero']."""
        return [s.strip() for s in (self.tema or '').split('/') if s.strip()]

    @property
    def raiz_id(self) -> int:
        """ID del archivo raíz del árbol de versiones (self si es raíz)."""
        return self.version_padre_id or self.id

    @property
    def es_raiz(self) -> bool:
        return self.version_padre_id is None


class ArchivoComparticion(models.Model):
    """
    Compartición explícita de un Archivo con un UsuarioPortal específico.
    Se suma a la visibilidad base (privado/perfil/publico) sin reemplazarla:
    si el archivo es PRIVADO, esta tabla lo expone solo a quienes están
    listados acá.

    Por ahora todo el acceso es "puede ver/descargar" (no hay nivel de
    edición). Si más adelante hace falta, agregar un campo `nivel`.
    """
    archivo = models.ForeignKey(
        Archivo, on_delete=models.CASCADE, related_name='comparticiones',
    )
    usuario = models.ForeignKey(
        'UsuarioPortal', on_delete=models.CASCADE, related_name='archivos_compartidos_con_mi',
    )
    compartido_por = models.ForeignKey(
        'UsuarioPortal', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='archivos_que_compartio',
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Compartición de archivo'
        verbose_name_plural = 'Comparticiones de archivo'
        ordering = ['-creado']
        constraints = [
            models.UniqueConstraint(
                fields=['archivo', 'usuario'],
                name='correos_arc_share_unique',
            ),
        ]

    def __str__(self):
        return f'{self.archivo_id} → {self.usuario_id}'


class ArchivoVinculo(models.Model):
    """
    Vincula un Archivo a un Correo existente (no es un adjunto SMTP — es
    una asociación lógica). Útil para "este contrato pertenece al hilo del
    cliente X" sin re-subir el archivo.

    El correo no se modifica. La UI del detalle del correo lista los
    archivos vinculados y permite quitarlos.
    """
    archivo = models.ForeignKey(
        Archivo, on_delete=models.CASCADE, related_name='vinculos',
    )
    correo  = models.ForeignKey(
        Correo, on_delete=models.CASCADE, related_name='archivos_vinculados',
    )
    vinculado_por = models.ForeignKey(
        'UsuarioPortal', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='archivos_que_vinculo',
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Vínculo archivo↔correo'
        verbose_name_plural = 'Vínculos archivo↔correo'
        ordering = ['-creado']
        constraints = [
            models.UniqueConstraint(
                fields=['archivo', 'correo'],
                name='correos_arc_corr_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['correo', '-creado'], name='correos_arc_corr_idx'),
        ]

    def __str__(self):
        return f'arc={self.archivo_id} ↔ correo={self.correo_id}'


class UserDesktopPrefs(models.Model):
    """
    Layout personalizado del escritorio por usuario. JSON con orden + visibilidad
    de íconos de apps y widgets.

    Estructura esperada del `layout_json`:
        {
          "icons":   ["correos", "archivos", "contratos", "taller", "papelera", "ajustes"],
          "widgets": [
            {"id": "stats",          "visible": true},
            {"id": "ultimos_correos","visible": true},
            {"id": "top_temas",      "visible": true},
            {"id": "top_perfiles",   "visible": true},
            {"id": "proximas_citas", "visible": true},
            {"id": "archivos_recientes","visible": false}
          ]
        }

    Si el JSON está vacío o no tiene una clave, se usa el default del template.
    """
    usuario = models.OneToOneField(
        'UsuarioPortal',
        on_delete=models.CASCADE,
        related_name='desktop_prefs',
    )
    layout_json = models.JSONField(default=dict, blank=True)
    modificado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Preferencias de escritorio'
        verbose_name_plural = 'Preferencias de escritorio'

    def __str__(self):
        return f'Prefs de {self.usuario.email}'
