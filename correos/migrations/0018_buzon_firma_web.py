"""
Migration 0018: campo firma_web en Buzon (URL opcional para firmas).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0017_buzon_firma'),
    ]

    operations = [
        migrations.AddField(
            model_name='buzon',
            name='firma_web',
            field=models.CharField(
                max_length=120, blank=True, default='',
                help_text='URL del sitio web (ej. www.pietramonte.cl). Opcional.',
            ),
        ),
    ]
