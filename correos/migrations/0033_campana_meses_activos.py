"""
Migración 0033: agrega meses_activos a CampanaCorreo.

Permite programación más flexible: vacío = todos los meses (mensual),
[3,6,9,12] = trimestral, [6,12] = semestral, [1] = anual en enero.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0032_campanas_correos'),
    ]

    operations = [
        migrations.AddField(
            model_name='campanacorreo',
            name='meses_activos',
            field=models.JSONField(
                blank=True, default=list,
                help_text='Lista de meses 1-12 en que se envía. Vacío = todos (mensual). '
                          '[3,6,9,12]=trimestral, [6,12]=semestral, [1]=anual en enero.',
            ),
        ),
    ]
