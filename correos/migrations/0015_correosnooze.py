"""
Migration 0015: CorreoSnooze.
Snooze per-usuario para postponer correos en la bandeja.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0014_correo_idx_tiene_adjunto'),
    ]

    operations = [
        migrations.CreateModel(
            name='CorreoSnooze',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('until_at', models.DateTimeField(db_index=True, help_text='El correo está oculto hasta este momento.')),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('correo', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='snoozes',
                    to='correos.correo',
                )),
                ('usuario', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='correos_snoozed',
                    to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Correo pospuesto (snooze)',
                'verbose_name_plural': 'Correos pospuestos',
                'unique_together': {('usuario', 'correo')},
                'indexes': [
                    models.Index(fields=['usuario', 'until_at'], name='correos_snz_usr_until_idx'),
                ],
            },
        ),
    ]
