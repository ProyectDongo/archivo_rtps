"""
0022 — Constraint UNIQUE partial sobre Correo.(buzon, mensaje_id)
       cuando mensaje_id != ''.

Defensa final contra duplicación de correos al sincronizar desde Gmail.
Postgres rechaza al segundo INSERT con el mismo Message-ID en el mismo
buzón. Cubre el caso de race condition entre 2 sync corriendo en paralelo,
que el set en memoria no puede ver.

Condition `mensaje_id != ''` porque algunos correos viejos (importados
desde mbox o malformados) carecen de Message-ID header. La constraint
no aplica a ellos (no podemos garantizar dedup sin Message-ID; los
protege el cursor last_uid del sync).

**IMPORTANTE — ANTES DE MIGRAR EN PROD:** correr el management command
`detectar_correos_duplicados` para detectar duplicados existentes que
harían fallar la creación de la constraint. Ver docstring del comando
para opciones de limpieza segura.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0021_categoria_tema_userdesktopprefs'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='correo',
            constraint=models.UniqueConstraint(
                fields=['buzon', 'mensaje_id'],
                condition=~models.Q(mensaje_id=''),
                name='unique_correo_buzon_msgid',
            ),
        ),
    ]
