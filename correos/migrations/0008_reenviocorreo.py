"""
Modelo ReenvioCorreo: bitácora + audit log de cada reenvío que un
UsuarioPortal dispara desde el portal.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0007_correo_tipo_carpeta'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReenvioCorreo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('destinatarios', models.TextField(help_text='Emails coma-separados a los que se envió.')),
                ('mensaje_extra', models.TextField(
                    blank=True, max_length=2000,
                    help_text='Nota que el usuario agregó arriba del correo original.')),
                ('enviado_en', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('exito', models.BooleanField(
                    db_index=True, default=False,
                    help_text='True si el envío SMTP completó sin error.')),
                ('error_msg', models.TextField(
                    blank=True, max_length=500,
                    help_text='Mensaje de error si el envío falló.')),
                ('ip_hash', models.CharField(blank=True, db_index=True, max_length=64)),
                ('correo', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reenvios',
                    to='correos.correo')),
                ('usuario', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reenvios_realizados',
                    to='correos.usuarioportal',
                    help_text='Usuario que disparó el reenvío. NULL si el usuario fue eliminado.')),
            ],
            options={
                'verbose_name': 'Reenvío de correo',
                'verbose_name_plural': 'Reenvíos de correos',
                'ordering': ['-enviado_en'],
                'indexes': [
                    models.Index(fields=['usuario', '-enviado_en'], name='correos_ree_usuario_a8c3f1_idx'),
                    models.Index(fields=['exito', '-enviado_en'], name='correos_ree_exito_b2d96e_idx'),
                ],
            },
        ),
    ]
