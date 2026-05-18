"""
Migración 0035: agrega flag puede_taller a UsuarioPortal.

Permite que usuarios no-admin gestionen el catálogo del taller (servicios,
repuestos) y vean/confirmen reservas desde el escritorio (módulo "Taller").
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0034_alter_campanacorreo_hora_envio_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuarioportal',
            name='puede_taller',
            field=models.BooleanField(
                default=False,
                help_text='Permite gestionar el catálogo del taller (servicios, repuestos) '
                          'y ver/confirmar reservas. Los admins siempre pueden.',
            ),
        ),
    ]
