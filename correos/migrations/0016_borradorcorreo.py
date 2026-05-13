"""
Migration 0016: BorradorCorreo.
Borradores per-usuario de correos en redacción (compose flotante + reply inline).
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0015_correosnooze'),
    ]

    operations = [
        migrations.CreateModel(
            name='BorradorCorreo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('modo', models.CharField(
                    max_length=20,
                    choices=[
                        ('compose',         'Composición nueva'),
                        ('responder',       'Responder'),
                        ('responder_todos', 'Responder a todos'),
                        ('reenviar',        'Reenviar'),
                    ],
                    default='compose',
                )),
                ('to',          models.TextField(blank=True, default='')),
                ('cc',          models.TextField(blank=True, default='')),
                ('asunto',      models.CharField(max_length=1000, blank=True, default='')),
                ('cuerpo',      models.TextField(blank=True, default='', max_length=50000)),
                ('creado',      models.DateTimeField(auto_now_add=True)),
                ('actualizado', models.DateTimeField(auto_now=True)),
                ('usuario', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='borradores',
                    to='correos.usuarioportal',
                )),
                ('buzon', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='borradores',
                    to='correos.buzon',
                    help_text='Buzón desde el que se enviará el correo (define el From).',
                )),
                ('correo_original', models.ForeignKey(
                    on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                    related_name='borradores_de',
                    to='correos.correo',
                    help_text='Solo para responder/reenviar.',
                )),
            ],
            options={
                'verbose_name': 'Borrador de correo',
                'verbose_name_plural': 'Borradores de correos',
                'ordering': ['-actualizado'],
                'indexes': [
                    models.Index(fields=['usuario', '-actualizado'], name='correos_brd_usr_act_idx'),
                ],
            },
        ),
    ]
