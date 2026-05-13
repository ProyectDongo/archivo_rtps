"""
0024 — Modelo Archivo: base para apps Archivos, Contratos, Papelera.

Un solo modelo con campo `tipo` (doc / contrato / factura / imagen / otro)
en lugar de modelos separados. Razones:
  - DRY: upload, list, download, soft-delete son idénticos para todos.
  - Papelera unificada: lista archivos en papelera de TODAS las apps.
  - Migración futura simple: si separamos tipos en modelos distintos, es
    un FK + data migration, no rehacer la app.

Campos Contrato-only (vencimiento, partes) viven en el mismo modelo
porque son metadata extra; un archivo doc nunca los usa.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0023_usuarioportal_lockout'),
    ]

    operations = [
        migrations.CreateModel(
            name='Archivo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(
                    max_length=200,
                    help_text='Nombre descriptivo (no el filename).',
                )),
                ('archivo', models.FileField(upload_to='archivos/%Y/%m/')),
                ('mime_type', models.CharField(blank=True, max_length=200)),
                ('tamano_bytes', models.PositiveBigIntegerField(default=0)),
                ('tipo', models.CharField(
                    choices=[
                        ('doc', 'Documento'),
                        ('contrato', 'Contrato'),
                        ('factura', 'Factura'),
                        ('imagen', 'Imagen'),
                        ('otro', 'Otro'),
                    ],
                    db_index=True, default='doc', max_length=15,
                    help_text='Categoría del archivo. Define en qué app aparece.',
                )),
                ('tema', models.CharField(
                    blank=True, db_index=True, default='', max_length=80,
                    help_text='Tema/carpeta libre (ej. Facturación, Proveedores).',
                )),
                ('fecha', models.DateField(
                    blank=True, db_index=True, null=True,
                    help_text='Fecha del documento (no la de upload).',
                )),
                ('descripcion', models.TextField(blank=True, default='')),
                ('contrato_partes', models.CharField(
                    blank=True, default='', max_length=300,
                    help_text='Partes firmantes (separadas por ;). Solo para tipo=contrato.',
                )),
                ('contrato_vencimiento', models.DateField(
                    blank=True, db_index=True, null=True,
                    help_text='Fecha de vencimiento. Solo para tipo=contrato.',
                )),
                ('creado', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('modificado', models.DateTimeField(auto_now=True)),
                ('eliminado_en', models.DateTimeField(
                    blank=True, db_index=True, null=True,
                    help_text='Si no es null, el archivo está en la papelera.',
                )),
                ('perfil', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='archivos', to='correos.buzon',
                    help_text='Perfil/responsable. Si vacío, el archivo es "Compartido".',
                )),
                ('creado_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='archivos_subidos', to='correos.usuarioportal',
                )),
                ('eliminado_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='archivos_eliminados', to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Archivo',
                'verbose_name_plural': 'Archivos',
                'ordering': ['-creado'],
                'indexes': [
                    models.Index(fields=['tipo', '-creado'],  name='correos_arc_tipo_idx'),
                    models.Index(fields=['perfil', '-creado'], name='correos_arc_perfil_idx'),
                    models.Index(fields=['eliminado_en'],     name='correos_arc_elim_idx'),
                ],
            },
        ),
    ]
