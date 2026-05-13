"""
Migration 0012: Correo.cuerpo_html
Cuerpo en HTML del correo (cuando lo tiene). Texto plano queda en cuerpo_texto
para búsqueda; cuerpo_html para display rendereado. Vacío en correos viejos
hasta que se haga backfill (separado).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0011_correoenviado'),
    ]

    operations = [
        migrations.AddField(
            model_name='correo',
            name='cuerpo_html',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Cuerpo en HTML si el correo lo tenía. Vacío si era solo texto plano. '
                          'Se sanitiza con bleach al renderizar (no acá) — guardarse crudo está bien.',
            ),
        ),
    ]
