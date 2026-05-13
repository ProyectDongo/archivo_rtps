"""
Index (buzon, tiene_adjunto) en Correo. Antes:
  - Stats `con_adjuntos = qs.filter(tiene_adjunto=True).count()` era seq scan
    parcial sobre todos los correos del buzón (~3928 correos × 8 buzones).
  - Filtro ?adjuntos=1 en inbox_view sufría lo mismo.

Con índice compuesto:
  - PG usa index-only scan o bitmap → 1-3 ms en vez de 30-100 ms.

Nombre del índice hardcodeado para que matchee con models.py exactamente
(memoria feedback_django_migration_index_names.md).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0013_adjunto_content_id'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='correo',
            index=models.Index(
                fields=['buzon', 'tiene_adjunto'],
                name='correos_cor_buzon_a_d2f8e1_idx',
            ),
        ),
    ]
