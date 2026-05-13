"""
0025 — Archivo.visibilidad explícita (privado/perfil/publico).

Antes el modelo era ambiguo: "perfil=None" significaba "compartido con
todos", pero también podía interpretarse como "privado del uploader".
Ahora `visibilidad` es explícita y queda escrita en la DB.

Defaults para registros existentes:
- Si el archivo TENÍA perfil asignado → visibilidad='perfil' (sigue
  igual: lo ven users con acceso al buzón).
- Si NO tenía perfil → visibilidad='publico' (mantiene el comportamiento
  anterior de "todos lo ven").
- Ningún archivo arranca como 'privado' (eso lo elige el user al subir).

Esto permite que de aquí en adelante los uploads nuevos puedan ser
'privado' explícitamente sin romper retro-compat.
"""
from django.db import migrations, models


def set_visibilidad_inicial(apps, schema_editor):
    """Backfill: perfil=null → publico, perfil!=null → perfil."""
    Archivo = apps.get_model('correos', 'Archivo')
    Archivo.objects.filter(perfil__isnull=True).update(visibilidad='publico')
    Archivo.objects.filter(perfil__isnull=False).update(visibilidad='perfil')


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0024_archivo'),
    ]

    operations = [
        migrations.AddField(
            model_name='archivo',
            name='visibilidad',
            field=models.CharField(
                choices=[
                    ('privado', 'Privado · solo yo y admins'),
                    ('perfil',  'Por perfil · users con acceso al buzón'),
                    ('publico', 'Público · todos los users del portal'),
                ],
                db_index=True,
                default='perfil',
                max_length=10,
                help_text='Privado: solo yo + admins. Perfil: users con acceso al buzón. '
                          'Público: todos los users del portal.',
            ),
        ),
        migrations.RunPython(set_visibilidad_inicial, reverse_noop),
    ]
