"""
Migration 0009: BuzonGmailLabel.
Mapeo label de Gmail → Buzon para sync IMAP automático.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0008_reenviocorreo'),
    ]

    operations = [
        migrations.CreateModel(
            name='BuzonGmailLabel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label_name', models.CharField(
                    help_text='Nombre EXACTO del label en Gmail. Case-sensitive. Para ver los disponibles, usá la action "Listar labels disponibles" en este admin.',
                    max_length=200,
                )),
                ('tipo_carpeta', models.CharField(
                    choices=[('inbox', 'Bandeja de entrada'), ('enviados', 'Enviados'), ('otros', 'Otros / sin clasificar')],
                    default='inbox',
                    help_text='Bajo qué pestaña aparecen estos correos en el portal.',
                    max_length=10,
                )),
                ('activo', models.BooleanField(default=True)),
                ('last_uid', models.PositiveBigIntegerField(
                    default=0,
                    help_text='UID del último mensaje IMAP sincronizado. 0 = traer toda la historia del label en la próxima corrida.',
                )),
                ('last_sync_at', models.DateTimeField(blank=True, null=True)),
                ('correos_sincronizados', models.IntegerField(default=0)),
                ('error_msg', models.TextField(
                    blank=True,
                    help_text='Último error de sync (si hay).',
                    max_length=1000,
                )),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('buzon', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='gmail_labels',
                    to='correos.buzon',
                )),
            ],
            options={
                'verbose_name': 'Sync Gmail label → buzón',
                'verbose_name_plural': 'Sync Gmail labels → buzones',
                'ordering': ['buzon__email', 'label_name'],
                'unique_together': {('buzon', 'label_name')},
            },
        ),
    ]
