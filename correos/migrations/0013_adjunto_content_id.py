"""
Agrega Adjunto.content_id para resolver cid:xxx en HTML.

Sin este campo, los emails con imágenes inline mostraban `[cid:5db3...]`
como texto literal — tanto en el portal como en los forwards.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0012_correo_cuerpo_html'),
    ]

    operations = [
        migrations.AddField(
            model_name='adjunto',
            name='content_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Content-ID (sin <>) del adjunto inline. Vacío para attachments normales.',
                max_length=300,
            ),
        ),
    ]
