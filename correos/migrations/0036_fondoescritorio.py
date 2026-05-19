"""
Migración 0036: modelo FondoEscritorio para fondos rotativos del escritorio
gestionables desde la UI (upload via ImageField a MEDIA_ROOT).
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0035_usuarioportal_puede_taller'),
    ]

    operations = [
        migrations.CreateModel(
            name='FondoEscritorio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(
                    blank=True, default='', max_length=120,
                    help_text='Nombre amigable para identificarla en el panel (ej. "Habitat 67"). Opcional.',
                )),
                ('imagen', models.ImageField(
                    upload_to='escritorio_bg/',
                    help_text='Recomendado: 1920×1080 mínimo, JPG/WEBP comprimido a 300-700 KB.',
                )),
                ('activa', models.BooleanField(
                    default=True,
                    help_text='Si está apagada, no entra en la rotación pero queda guardada.',
                )),
                ('subida_en', models.DateTimeField(auto_now_add=True)),
                ('subida_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='fondos_escritorio_subidos', to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Fondo de escritorio',
                'verbose_name_plural': 'Fondos de escritorio',
                'ordering': ['-subida_en'],
                'indexes': [models.Index(fields=['activa'], name='correos_fondo_act_idx')],
            },
        ),
    ]
