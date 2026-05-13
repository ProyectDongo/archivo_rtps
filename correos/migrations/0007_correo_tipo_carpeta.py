"""
Agrega `tipo_carpeta` a Correo: separa Inbox / Enviados / Otros para que la
búsqueda en el portal sea más útil. Default 'otros' para no romper los
2146 correos ya importados — el comando `clasificar_correos` los reclasifica
con heurística sobre `remitente` vs `buzon.email`.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0006_2fa'),
    ]

    operations = [
        migrations.AddField(
            model_name='correo',
            name='tipo_carpeta',
            field=models.CharField(
                choices=[
                    ('inbox', 'Bandeja de entrada'),
                    ('enviados', 'Enviados'),
                    ('otros', 'Otros / sin clasificar'),
                ],
                db_index=True,
                default='otros',
                max_length=10,
            ),
        ),
        migrations.AddIndex(
            model_name='correo',
            index=models.Index(
                fields=['buzon', 'tipo_carpeta', '-fecha'],
                name='correos_cor_buzon_i_2c8a4f_idx',
            ),
        ),
    ]
