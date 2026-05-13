"""
0021 — Fase 1 del rediseño escritorio:
  - CategoriaTema: clasificación de temas mencionados en correos
    (widget "Top temas" del escritorio). Editable.
  - UserDesktopPrefs: layout personalizado del escritorio por usuario.

Incluye seed inicial con 7 categorías por defecto (cotización, repuesto,
recibo, factura, cancelación, reclamo, pedido). El admin las puede
editar después desde el admin Django o desde Ajustes (Fase 1.5).
"""
from django.db import migrations, models


CATEGORIAS_DEFAULT = [
    # (orden, nombre, color, keywords)
    (10, 'Cotizaciones',         '#C80C0F', 'cotización, cotizacion, presupuesto, quote, quotation'),
    (20, 'Pedidos de repuestos', '#1976D2', 'repuesto, repuestos, parts, refacción, refaccion, OC, orden de compra'),
    (30, 'Recibos / pagos',      '#388E3C', 'recibo, pago, transferencia, depósito, deposito, abono, acuso recibo'),
    (40, 'Facturas',             '#F57C00', 'factura, invoice, boleta, dte, electrónica, electronica'),
    (50, 'Cancelaciones',        '#7B1FA2', 'cancelación, cancelacion, anulación, anulacion, suspender'),
    (60, 'Reclamos',             '#D32F2F', 'reclamo, queja, problema, falla, defecto'),
    (70, 'Servicio / mantención',           '#0288D1', 'servicio, mantención, mantencion, revisión, revision, reparación, reparacion'),
]


def seed_categorias(apps, schema_editor):
    Categoria = apps.get_model('correos', 'CategoriaTema')
    for orden, nombre, color, keywords in CATEGORIAS_DEFAULT:
        Categoria.objects.update_or_create(
            nombre=nombre,
            defaults={
                'orden':    orden,
                'color':    color,
                'keywords': keywords,
                'activa':   True,
            },
        )


def borrar_categorias(apps, schema_editor):
    """Rollback: borra las categorías seedeadas (las que el admin agregó después se quedan)."""
    Categoria = apps.get_model('correos', 'CategoriaTema')
    Categoria.objects.filter(nombre__in=[n for _, n, _, _ in CATEGORIAS_DEFAULT]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0020_borrador_adjunto'),
    ]

    operations = [
        migrations.CreateModel(
            name='CategoriaTema',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=80, unique=True)),
                ('keywords', models.TextField(
                    help_text='Palabras clave separadas por coma o saltos de línea. '
                              'Match case-insensitive contra asunto + cuerpo del correo.',
                )),
                ('color', models.CharField(
                    default='#C80C0F', max_length=7,
                    help_text='Color hex del chip en el widget (ej. #C80C0F).',
                )),
                ('orden', models.PositiveSmallIntegerField(
                    default=100,
                    help_text='Menor = aparece más arriba en el widget Top temas.',
                )),
                ('activa', models.BooleanField(
                    default=True,
                    help_text='Si está desactivada, no aparece en el widget ni se cuenta.',
                )),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('modificado', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Categoría de tema',
                'verbose_name_plural': 'Categorías de tema',
                'ordering': ['orden', 'nombre'],
                'indexes': [
                    models.Index(fields=['activa', 'orden'], name='correos_cat_act_ord_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='UserDesktopPrefs',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('layout_json', models.JSONField(blank=True, default=dict)),
                ('modificado', models.DateTimeField(auto_now=True)),
                ('usuario', models.OneToOneField(
                    on_delete=models.deletion.CASCADE,
                    related_name='desktop_prefs',
                    to='correos.usuarioportal',
                )),
            ],
            options={
                'verbose_name': 'Preferencias de escritorio',
                'verbose_name_plural': 'Preferencias de escritorio',
            },
        ),
        migrations.RunPython(seed_categorias, reverse_code=borrar_categorias),
    ]
