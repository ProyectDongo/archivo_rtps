"""
Migration 0011: CorreoEnviado.
Bitácora de respuestas/composiciones nuevas enviadas desde el portal.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0010_correoleido'),
    ]

    operations = [
        migrations.CreateModel(
            name='CorreoEnviado',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(
                    choices=[
                        ('responder', 'Responder'),
                        ('responder_todos', 'Responder a todos'),
                        ('compose', 'Composición nueva'),
                    ],
                    default='responder',
                    db_index=True,
                    max_length=20,
                )),
                ('destinatarios', models.TextField(help_text='Emails del To, coma-separados.')),
                ('cc', models.TextField(blank=True, help_text='Emails del Cc, coma-separados.')),
                ('asunto', models.CharField(max_length=1000)),
                ('cuerpo', models.TextField(blank=True, help_text='Body que escribió el usuario (sin el quote del original).')),
                ('mensaje_id', models.CharField(blank=True, help_text='Message-ID que generamos para este envío.', max_length=500)),
                ('in_reply_to', models.CharField(blank=True, help_text='Message-ID del correo al que respondemos.', max_length=500)),
                ('enviado_en', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('exito', models.BooleanField(db_index=True, default=False)),
                ('error_msg', models.TextField(blank=True, max_length=500)),
                ('ip_hash', models.CharField(blank=True, db_index=True, max_length=64)),
                ('buzon', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='correos_enviados',
                    to='correos.buzon',
                    help_text='Buzón desde el que se envió (define el From).',
                )),
                ('correo_guardado', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='entrada_envio',
                    to='correos.correo',
                    help_text='Copia guardada en BD con tipo_carpeta=enviados.',
                )),
                ('correo_original', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='respuestas',
                    to='correos.correo',
                    help_text='Correo al que se respondió. NULL si fue compose nuevo.',
                )),
                ('usuario', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='enviados_realizados',
                    to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Correo enviado desde el portal',
                'verbose_name_plural': 'Correos enviados desde el portal',
                'ordering': ['-enviado_en'],
                'indexes': [
                    models.Index(fields=['usuario', '-enviado_en'], name='correos_cor_usuario_4f8d2a_idx'),
                    models.Index(fields=['buzon', '-enviado_en'], name='correos_cor_buzon_i_e1c39b_idx'),
                    models.Index(fields=['exito', '-enviado_en'], name='correos_cor_exito_3a7e4f_idx'),
                ],
            },
        ),
    ]
