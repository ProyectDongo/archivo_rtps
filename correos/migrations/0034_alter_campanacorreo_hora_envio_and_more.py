"""
Migración 0034: ajustes cosméticos detectados por Django.

1. `hora_envio` default ahora es `datetime.time(9, 0)` (era string '09:00').
   Funcionalmente equivalente — Django normaliza ambos al mismo valor en DB.
2. Convierte `id` AutoField → BigAutoField en los 4 modelos nuevos para
   cumplir con DEFAULT_AUTO_FIELD = 'BigAutoField' del proyecto.

Operaciones no-destructivas: SQLite y Postgres pueden alterar AutoField a
BigAutoField sin downtime ni reescritura de tabla.
"""
import datetime
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0033_campana_meses_activos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='campanacorreo',
            name='hora_envio',
            field=models.TimeField(
                default=datetime.time(9, 0),
                help_text='Hora local Chile en que arranca el envío. El cron debe correr cerca de esta hora.',
            ),
        ),
        migrations.AlterField(
            model_name='campanacorreo',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        migrations.AlterField(
            model_name='contactolista',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        migrations.AlterField(
            model_name='enviocampana',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        migrations.AlterField(
            model_name='listadestinatarios',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
    ]
