"""
0026 — Versiones de Archivo + comparticiones explícitas + vínculos a Correo.

Suma 3 piezas al modelo Archivo:
- `version_padre` (FK self) + `version_num` + `version_nota`: cada versión es
  un row independiente; v1 tiene padre=NULL, v2+ apuntan al raíz. NO se
  encadena v3→v2→v1, todos apuntan al raíz para que `SELECT versiones de X`
  sea un solo query.
- `ArchivoComparticion`: tabla M2M Archivo↔UsuarioPortal con audit (quién
  compartió, cuándo). Suma a la visibilidad base sin reemplazarla. Permite
  exponer un archivo PRIVADO solo a usuarios específicos.
- `ArchivoVinculo`: M2M Archivo↔Correo. Asociación lógica (NO se reupload
  como adjunto SMTP). Para que "este contrato vive en el hilo del cliente X".

Defaults: archivos existentes quedan como version_num=1 con padre=NULL
(son su propia raíz). No se crean comparticiones ni vínculos retro.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0025_archivo_visibilidad'),
    ]

    operations = [
        # ─── Versiones en Archivo ──────────────────────────────────────────
        migrations.AddField(
            model_name='archivo',
            name='version_padre',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.CASCADE,
                related_name='versiones',
                to='correos.archivo',
                db_index=True,
                help_text='Archivo raíz si esto es una versión posterior. '
                          'NULL = es la raíz (puede tener versiones hijas).',
            ),
        ),
        migrations.AddField(
            model_name='archivo',
            name='version_num',
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text='Número de versión (1 = original).',
            ),
        ),
        migrations.AddField(
            model_name='archivo',
            name='version_nota',
            field=models.CharField(
                max_length=300, blank=True, default='',
                help_text='Qué cambió en esta versión (opcional).',
            ),
        ),
        migrations.AddIndex(
            model_name='archivo',
            index=models.Index(
                fields=['version_padre', '-version_num'],
                name='correos_arc_ver_idx',
            ),
        ),

        # ─── ArchivoComparticion ───────────────────────────────────────────
        migrations.CreateModel(
            name='ArchivoComparticion',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('archivo', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='comparticiones',
                    to='correos.archivo',
                )),
                ('usuario', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='archivos_compartidos_con_mi',
                    to='correos.usuarioportal',
                )),
                ('compartido_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='archivos_que_compartio',
                    to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Compartición de archivo',
                'verbose_name_plural': 'Comparticiones de archivo',
                'ordering': ['-creado'],
            },
        ),
        migrations.AddConstraint(
            model_name='archivocomparticion',
            constraint=models.UniqueConstraint(
                fields=['archivo', 'usuario'],
                name='correos_arc_share_unique',
            ),
        ),

        # ─── ArchivoVinculo ────────────────────────────────────────────────
        migrations.CreateModel(
            name='ArchivoVinculo',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('archivo', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='vinculos',
                    to='correos.archivo',
                )),
                ('correo', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='archivos_vinculados',
                    to='correos.correo',
                )),
                ('vinculado_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='archivos_que_vinculo',
                    to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Vínculo archivo↔correo',
                'verbose_name_plural': 'Vínculos archivo↔correo',
                'ordering': ['-creado'],
            },
        ),
        migrations.AddConstraint(
            model_name='archivovinculo',
            constraint=models.UniqueConstraint(
                fields=['archivo', 'correo'],
                name='correos_arc_corr_unique',
            ),
        ),
        migrations.AddIndex(
            model_name='archivovinculo',
            index=models.Index(
                fields=['correo', '-creado'],
                name='correos_arc_corr_idx',
            ),
        ),
    ]
