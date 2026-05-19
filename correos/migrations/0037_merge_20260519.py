"""
Merge migration: unifica las dos ramas 0036 que quedaron paralelas.

- 0036_account_lockout_accion: agrega choices nuevos a EventoAuditoria.accion
- 0036_fondoescritorio: crea modelo FondoEscritorio para fondos rotativos

Ambas dependian de 0035_usuarioportal_puede_taller. Este merge las unifica
sin operations propias para que Django pueda continuar el grafo lineal.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0036_account_lockout_accion'),
        ('correos', '0036_fondoescritorio'),
    ]

    operations = []
