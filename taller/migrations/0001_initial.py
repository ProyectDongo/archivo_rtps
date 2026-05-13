"""
Migración inicial de la app `taller`:
  - ItemCatalogo  (catálogo unificado de servicios + repuestos)
  - BloqueoCalendario  (feriados y vacaciones)
  - Reserva  (cita del cliente; FK a auth.User para confirmaciones por llamada)
  - ReservaIntento  (bitácora del form público)

Todos los choices se enumeran explícitamente para que esta migración no
dependa del módulo de modelos en ejecuciones futuras.
"""
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import taller.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ─── ItemCatalogo ──────────────────────────────────────────────
        migrations.CreateModel(
            name='ItemCatalogo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(
                    choices=[('servicio', 'Servicio'), ('repuesto', 'Repuesto')],
                    default='servicio', max_length=10)),
                ('categoria', models.CharField(
                    choices=[
                        ('mantencion', 'Mantención preventiva'),
                        ('lubricacion', 'Lubricación y filtros'),
                        ('frenos', 'Frenos y embrague'),
                        ('suspension', 'Suspensión y dirección'),
                        ('electrico', 'Eléctrico y electrónico'),
                        ('motor', 'Motor y transmisión'),
                        ('neumaticos', 'Neumáticos y alineación'),
                        ('aire', 'Aire acondicionado'),
                        ('diagnostico', 'Diagnóstico computacional'),
                        ('revision_tecnica', 'Revisión técnica'),
                        ('camionetas_4x4', 'Camionetas / 4x4 / Minería'),
                        ('carroceria', 'Carrocería y pintura'),
                        ('detailing', 'Lavado y detailing'),
                        ('rep_aceites', 'Aceites y lubricantes'),
                        ('rep_filtros', 'Filtros'),
                        ('rep_frenos', 'Frenos (repuestos)'),
                        ('rep_electrico', 'Eléctrico (repuestos)'),
                        ('rep_iluminacion', 'Iluminación'),
                        ('rep_neumaticos', 'Neumáticos y llantas'),
                        ('rep_correas', 'Correas y kits distribución'),
                        ('rep_bujias', 'Bujías y encendido'),
                        ('rep_refrigeracion', 'Refrigeración'),
                        ('rep_limpieza', 'Limpiaparabrisas'),
                        ('rep_vidrios', 'Parabrisas y vidrios'),
                        ('rep_camionetas', 'Camionetas/Off-road/Minería'),
                        ('otros', 'Otros'),
                    ],
                    default='mantencion', max_length=30)),
                ('nombre', models.CharField(max_length=120)),
                ('descripcion', models.TextField(blank=True, max_length=2000)),
                ('imagen', models.ImageField(
                    blank=True, null=True, upload_to='catalogo/',
                    help_text='Foto del servicio/repuesto. Si está vacía, se usa el ícono.')),
                ('icono_lucide', models.CharField(
                    blank=True, default='wrench', max_length=40,
                    help_text='Nombre del ícono Lucide para el fallback (ej: wrench, oil-can, battery).')),
                ('precio_referencia_clp', models.PositiveIntegerField(
                    default=0,
                    help_text='Precio referencial en CLP. El final se confirma al revisar el vehículo.')),
                ('duracion_min', models.PositiveIntegerField(
                    default=30,
                    help_text='Duración estimada en minutos. 0 = solo repuesto sin instalación.')),
                ('disponibilidad', models.CharField(
                    choices=[
                        ('en_stock', 'En stock'),
                        ('bajo_pedido', 'Bajo pedido (3-5 días)'),
                        ('consultar', 'Consultar disponibilidad'),
                    ],
                    default='en_stock', max_length=20,
                    help_text='Solo informativo — el control de stock real se lleva aparte.')),
                ('marca_repuesto', models.CharField(
                    blank=True, default='', max_length=20,
                    choices=[
                        ('oem', 'OEM Original'),
                        ('premium', 'Premium compatible'),
                        ('generico', 'Genérico/económico'),
                    ],
                    help_text='Solo aplica a repuestos. En servicios, dejar vacío.')),
                ('destacado', models.BooleanField(
                    default=False, help_text='Marcar como "Popular" en el catálogo público.')),
                ('activo', models.BooleanField(
                    default=True,
                    help_text='Si está desactivado, no aparece en el catálogo público (sí en admin).')),
                ('orden', models.IntegerField(
                    default=0, help_text='Menor número = aparece primero. Empate = orden alfabético.')),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('actualizado', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Item del catálogo',
                'verbose_name_plural': 'Catálogo',
                'ordering': ['orden', 'nombre'],
                'indexes': [
                    models.Index(fields=['activo', 'tipo', 'categoria'], name='taller_item_activo__19a76e_idx'),
                    models.Index(fields=['destacado', 'activo'], name='taller_item_destaca_8b3f9c_idx'),
                ],
            },
        ),

        # ─── BloqueoCalendario ─────────────────────────────────────────
        migrations.CreateModel(
            name='BloqueoCalendario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha', models.DateField(db_index=True, unique=True)),
                ('motivo', models.CharField(
                    max_length=200,
                    help_text='Ej: "Día del trabajador", "Vacaciones del taller".')),
                ('fuente', models.CharField(
                    choices=[('api_gob', 'API gob.cl (automático)'), ('manual', 'Manual (admin)')],
                    default='manual', max_length=10)),
                ('activo', models.BooleanField(
                    default=True,
                    help_text='Desmarcar para "abrir" excepcionalmente un día normalmente bloqueado.')),
                ('creado', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Bloqueo de calendario',
                'verbose_name_plural': 'Bloqueos de calendario',
                'ordering': ['fecha'],
            },
        ),

        # ─── ReservaIntento ────────────────────────────────────────────
        migrations.CreateModel(
            name='ReservaIntento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip_hash', models.CharField(db_index=True, max_length=64)),
                ('user_agent', models.CharField(blank=True, max_length=500)),
                ('email_intentado', models.CharField(blank=True, max_length=254)),
                ('motivo', models.CharField(
                    max_length=20,
                    choices=[
                        ('exito', 'Reserva creada'),
                        ('turnstile_fail', 'Turnstile falló'),
                        ('captcha_fail', 'Captcha (Fernet) falló'),
                        ('email_invalido', 'Email inválido'),
                        ('email_desechable', 'Email desechable bloqueado'),
                        ('email_no_verif', 'No verificó código de email'),
                        ('telefono_invalido', 'Teléfono inválido'),
                        ('patente_invalida', 'Patente inválida'),
                        ('slot_ocupado', 'Slot ya tomado'),
                        ('slot_pasado', 'Slot en el pasado'),
                        ('fuera_horario', 'Fuera de horario laboral'),
                        ('feriado', 'Día bloqueado (feriado/vacaciones)'),
                        ('throttled', 'Bloqueado por rate-limit'),
                        ('honeypot', 'Honeypot lleno (bot)'),
                        ('otro', 'Otro'),
                    ])),
                ('exito', models.BooleanField(db_index=True, default=False)),
                ('creado', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'verbose_name': 'Intento de reserva',
                'verbose_name_plural': 'Intentos de reserva',
                'ordering': ['-creado'],
                'indexes': [
                    models.Index(fields=['ip_hash', '-creado'], name='taller_resi_ip_hash_4a2c8e_idx'),
                    models.Index(fields=['exito', '-creado'], name='taller_resi_exito_d5b1f7_idx'),
                ],
            },
        ),

        # ─── Reserva ───────────────────────────────────────────────────
        migrations.CreateModel(
            name='Reserva',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(
                    db_index=True, max_length=64, unique=True,
                    help_text='SHA-256 del token público que va en el email del cliente.')),
                ('cliente_nombre', models.CharField(max_length=120)),
                ('cliente_email', models.EmailField(db_index=True, max_length=254)),
                ('cliente_telefono', models.CharField(
                    max_length=20,
                    validators=[taller.models.telefono_validator])),
                ('patente', models.CharField(
                    db_index=True, max_length=10,
                    validators=[taller.models.patente_validator])),
                ('marca', models.CharField(
                    max_length=40, help_text='Ej: Toyota, Hyundai, Chevrolet.')),
                ('modelo', models.CharField(
                    max_length=60, help_text='Ej: Hilux, Tucson, Spark.')),
                ('anio', models.PositiveIntegerField(
                    blank=True, null=True,
                    validators=[django.core.validators.MinValueValidator(1950)],
                    help_text='Año del vehículo (opcional).')),
                ('motor', models.CharField(
                    blank=True, max_length=40,
                    help_text='Ej: 1.6L, 2.5 Diesel (opcional).')),
                ('kilometraje', models.PositiveIntegerField(
                    blank=True, null=True,
                    help_text='Kilometraje aproximado (opcional).')),
                ('contexto_problema', models.TextField(
                    blank=True, max_length=2000,
                    help_text='Descripción libre: cómo empezó el problema, ruidos, síntomas. Ayuda al diagnóstico.')),
                ('fecha', models.DateField(db_index=True)),
                ('hora_inicio', models.TimeField()),
                ('duracion_estimada_min', models.PositiveIntegerField(
                    default=30,
                    help_text='Suma de duraciones — solo informativo, no bloquea otros slots.')),
                ('total_referencial_clp', models.PositiveIntegerField(
                    default=0,
                    help_text='Suma de precios referenciales al momento de reservar.')),
                ('estado', models.CharField(
                    db_index=True, default='pendiente_email', max_length=25,
                    choices=[
                        ('pendiente_email', 'Pendiente de verificación por email'),
                        ('confirmada_email', 'Confirmada (email)'),
                        ('confirmada_llamada', 'Confirmada (llamada)'),
                        ('cancelada_cliente', 'Cancelada por el cliente'),
                        ('cancelada_taller', 'Cancelada por el taller'),
                        ('completada', 'Completada'),
                        ('no_show', 'No-show (no se presentó)'),
                    ])),
                ('creada_en', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('confirmada_email_en', models.DateTimeField(blank=True, null=True)),
                ('confirmada_llamada_en', models.DateTimeField(blank=True, null=True)),
                ('confirmada_llamada_nota', models.TextField(
                    blank=True, max_length=500,
                    help_text='Ej: "Habló con la esposa", "No contesta, dejé mensaje".')),
                ('cancelada_en', models.DateTimeField(blank=True, null=True)),
                ('cancelada_por', models.CharField(
                    blank=True, max_length=120,
                    help_text='"cliente" si la canceló él, o el username del admin que canceló.')),
                ('cancelada_motivo', models.CharField(blank=True, max_length=200)),
                ('reminder_24h_enviado_en', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('reminder_1h_enviado_en', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('ip_hash_creacion', models.CharField(blank=True, db_index=True, max_length=64)),
                ('user_agent_creacion', models.CharField(blank=True, max_length=500)),
                ('confirmada_llamada_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reservas_confirmadas',
                    to=settings.AUTH_USER_MODEL,
                    help_text='Admin que marcó la confirmación por llamada.')),
                ('items', models.ManyToManyField(
                    blank=True, related_name='reservas', to='taller.itemcatalogo',
                    help_text='Items elegidos del catálogo (servicios + repuestos).')),
            ],
            options={
                'verbose_name': 'Reserva',
                'verbose_name_plural': 'Reservas',
                'ordering': ['-fecha', '-hora_inicio'],
                'indexes': [
                    models.Index(fields=['fecha', 'hora_inicio'], name='taller_reser_fecha_h_3c8a2d_idx'),
                    models.Index(fields=['estado', '-creada_en'], name='taller_reser_estado_5e7b91_idx'),
                    models.Index(fields=['cliente_email', '-creada_en'], name='taller_reser_client_2a9f4c_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        condition=~models.Q(estado__in=['cancelada_cliente', 'cancelada_taller', 'no_show']),
                        fields=('fecha', 'hora_inicio'),
                        name='unique_slot_activo',
                    ),
                ],
            },
        ),
    ]
