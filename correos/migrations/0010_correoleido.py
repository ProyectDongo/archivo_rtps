"""
Migration 0010: CorreoLeido.
Marca per-usuario de "este correo lo leí". Existencia del registro = leído.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0009_buzongmaillabel'),
    ]

    operations = [
        migrations.CreateModel(
            name='CorreoLeido',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('leido_en', models.DateTimeField(auto_now_add=True)),
                ('correo', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='leidos_por',
                    to='correos.correo',
                )),
                ('usuario', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='correos_leidos',
                    to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Correo leído',
                'verbose_name_plural': 'Correos leídos',
                'unique_together': {('usuario', 'correo')},
                'indexes': [
                    models.Index(fields=['usuario', 'correo'], name='correos_cor_usuario_e8d39e_idx'),
                ],
            },
        ),
    ]
