"""
Threading robusto estilo Gmail.

Agrega:
- Modelo `Thread` (un hilo de conversación por buzón).
- `Correo.in_reply_to` y `Correo.references` (headers persistidos para
  reconstruir hilos al sincronizar nuevos correos).
- `Correo.thread` (FK al hilo al que pertenece).
- Índice compuesto para query de "último correo por hilo" en la bandeja.

Backfill: ver management command `recompute_threads`.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0027_rename_correos_cor_buzon_i_2c8a4f_idx_correos_cor_buzon_i_a5d3a1_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Thread',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('asunto', models.CharField(blank=True, max_length=1000)),
                ('mensaje_id_raiz', models.CharField(blank=True, db_index=True, max_length=500)),
                ('fecha_primero', models.DateTimeField(blank=True, null=True)),
                ('fecha_ultimo', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('count', models.IntegerField(default=0)),
                ('buzon', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='threads',
                    to='correos.buzon',
                )),
            ],
            options={
                'verbose_name': 'Hilo',
                'verbose_name_plural': 'Hilos',
                'ordering': ['-fecha_ultimo'],
            },
        ),
        migrations.AddIndex(
            model_name='thread',
            index=models.Index(fields=['buzon', '-fecha_ultimo'], name='correos_thr_buzon_ultimo_idx'),
        ),
        migrations.AddIndex(
            model_name='thread',
            index=models.Index(fields=['buzon', 'mensaje_id_raiz'], name='correos_thr_buzon_raiz_idx'),
        ),
        migrations.AddField(
            model_name='correo',
            name='in_reply_to',
            field=models.CharField(
                blank=True, db_index=True, default='', max_length=500,
                help_text='Header In-Reply-To del email (mensaje_id del padre).',
            ),
        ),
        migrations.AddField(
            model_name='correo',
            name='references',
            field=models.TextField(
                blank=True, default='',
                help_text='Header References completo (lista space-separated).',
            ),
        ),
        migrations.AddField(
            model_name='correo',
            name='thread',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='correos',
                to='correos.thread',
            ),
        ),
        migrations.AddIndex(
            model_name='correo',
            index=models.Index(fields=['buzon', 'thread', '-fecha'], name='correos_cor_buzon_thread_idx'),
        ),
    ]
