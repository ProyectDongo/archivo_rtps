"""
Papelera per-usuario para correos.

Modelo `CorreoEliminado(usuario, correo, eliminado_en, purgado)` que actúa
como flag soft-delete por usuario sin tocar el Correo original. Otros
usuarios del mismo buzón siguen viendo el correo.

Estados:
- record con purgado=False → correo está en papelera del usuario.
- record con purgado=True  → eliminación definitiva (sigue oculto).
- sin record               → correo visible para el usuario.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0028_thread'),
    ]

    operations = [
        migrations.CreateModel(
            name='CorreoEliminado',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True,
                                        serialize=False, verbose_name='ID')),
                ('eliminado_en', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('purgado', models.BooleanField(
                    db_index=True, default=False,
                    help_text='True = eliminado definitivamente (no en papelera). '
                              'False = en papelera, recuperable.',
                )),
                ('correo', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='eliminaciones',
                    to='correos.correo',
                )),
                ('usuario', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='correos_eliminados',
                    to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Correo eliminado',
                'verbose_name_plural': 'Correos eliminados',
                'unique_together': {('usuario', 'correo')},
            },
        ),
        migrations.AddIndex(
            model_name='correoeliminado',
            index=models.Index(
                fields=['usuario', 'purgado', '-eliminado_en'],
                name='correos_elim_usr_purg_idx',
            ),
        ),
    ]
