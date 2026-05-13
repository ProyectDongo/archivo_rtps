"""
Migration 0019: EventoAuditoria.
Bitácora de acciones sensibles per-usuario (snooze, etiquetas, firma, etc.).
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0018_buzon_firma_web'),
    ]

    operations = [
        migrations.CreateModel(
            name='EventoAuditoria',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('accion', models.CharField(
                    max_length=30, db_index=True,
                    choices=[
                        ('login_ok',         'Login exitoso'),
                        ('logout',           'Logout'),
                        ('password_cambio',  'Cambio de password'),
                        ('totp_setup',       '2FA configurado'),
                        ('totp_reset',       '2FA reseteado'),
                        ('recovery_regen',   'Recovery codes regenerados'),
                        ('bulk_leer',        'Marcar leídos en masa'),
                        ('bulk_no_leer',     'Marcar no-leídos en masa'),
                        ('bulk_destacar',    'Destacar en masa'),
                        ('bulk_etiquetar',   'Etiquetar en masa'),
                        ('snooze',           'Posponer correo'),
                        ('unsnooze',         'Cancelar snooze'),
                        ('etiqueta_crear',   'Etiqueta creada'),
                        ('etiqueta_asignar', 'Etiqueta asignada a correo'),
                        ('etiqueta_quitar',  'Etiqueta quitada de correo'),
                        ('firma_actualizar', 'Firma del buzón actualizada'),
                        ('borrador_crear',   'Borrador creado'),
                        ('borrador_borrar',  'Borrador descartado'),
                    ],
                )),
                ('target_tipo', models.CharField(max_length=20, blank=True, default='',
                                                 help_text='Modelo afectado (correo, buzon, etiqueta, borrador, …).')),
                ('target_id',   models.PositiveIntegerField(null=True, blank=True,
                                                            help_text='PK del objeto afectado.')),
                ('meta',        models.JSONField(default=dict, blank=True,
                                                 help_text='Detalle libre: ids, valores, etc.')),
                ('ip_hash',     models.CharField(max_length=64, blank=True, default='', db_index=True)),
                ('creado',      models.DateTimeField(auto_now_add=True, db_index=True)),
                ('usuario', models.ForeignKey(
                    on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                    related_name='eventos_auditoria',
                    to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Evento de auditoría',
                'verbose_name_plural': 'Eventos de auditoría',
                'ordering': ['-creado'],
                'indexes': [
                    models.Index(fields=['usuario', '-creado'], name='correos_evt_usr_act_idx'),
                    models.Index(fields=['accion',  '-creado'], name='correos_evt_acc_act_idx'),
                ],
            },
        ),
    ]
