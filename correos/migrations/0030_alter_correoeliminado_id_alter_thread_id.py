"""
Alinea el tipo del campo `id` en Thread y CorreoEliminado con el setting
`DEFAULT_AUTO_FIELD = BigAutoField` del proyecto. Las migraciones 0028 y
0029 los habían declarado como AutoField (int4) por error de copia — esto
los promueve a BigAutoField (int8), igual que el resto de las tablas.

Postgres ejecuta un ALTER COLUMN TYPE bigint. Con la cantidad de filas
actuales (Thread ~120, CorreoEliminado vacío) corre en milisegundos.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0029_correoeliminado'),
    ]

    operations = [
        migrations.AlterField(
            model_name='correoeliminado',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True,
                                      serialize=False, verbose_name='ID'),
        ),
        migrations.AlterField(
            model_name='thread',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True,
                                      serialize=False, verbose_name='ID'),
        ),
    ]
