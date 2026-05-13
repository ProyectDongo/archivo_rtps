"""
Migration 0017: campos de firma en Buzon.
Auto-append en correos salientes; cada buzón configura su firma desde el portal.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0016_borradorcorreo'),
    ]

    operations = [
        migrations.AddField(
            model_name='buzon',
            name='firma_activa',
            field=models.BooleanField(
                default=True,
                help_text='Si está desactivada, no se agrega firma al enviar.',
            ),
        ),
        migrations.AddField(
            model_name='buzon',
            name='firma_nombre',
            field=models.CharField(
                max_length=120, blank=True, default='',
                help_text='Nombre que aparece en la firma. Si vacío, solo se muestra el logo y el email.',
            ),
        ),
        migrations.AddField(
            model_name='buzon',
            name='firma_cargo',
            field=models.CharField(
                max_length=120, blank=True, default='Pietramonte Automotriz',
                help_text='Empresa o cargo (ej. "Pietramonte Automotriz").',
            ),
        ),
        migrations.AddField(
            model_name='buzon',
            name='firma_telefono',
            field=models.CharField(max_length=40, blank=True, default=''),
        ),
        migrations.AddField(
            model_name='buzon',
            name='firma_email_visible',
            field=models.EmailField(
                max_length=254, blank=True, default='',
                help_text='Email mostrado en la firma. Si vacío, usa el email del buzón.',
            ),
        ),
    ]
