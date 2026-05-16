"""
Migración 0031: índice GIN con pg_trgm para búsqueda full-text rápida.

Solo aplica en Postgres — se saltea silenciosamente en SQLite (dev/tests).
"""
from django.db import migrations


def _run_if_postgres(sql, reverse_sql=""):
    """Devuelve un RunSQL que solo ejecuta en Postgres, no-op en SQLite."""
    def forward(apps, schema_editor):
        if schema_editor.connection.vendor == 'postgresql':
            schema_editor.execute(sql)

    def backward(apps, schema_editor):
        if schema_editor.connection.vendor == 'postgresql' and reverse_sql:
            schema_editor.execute(reverse_sql)

    return migrations.RunPython(forward, backward)


class Migration(migrations.Migration):

    dependencies = [
        ('correos', '0030_alter_correoeliminado_id_alter_thread_id'),
    ]

    operations = [
        _run_if_postgres("CREATE EXTENSION IF NOT EXISTS pg_trgm;"),
        _run_if_postgres(
            sql="""
                CREATE INDEX IF NOT EXISTS correos_correo_asunto_trgm_idx
                ON correos_correo USING GIN (asunto gin_trgm_ops);
            """,
            reverse_sql="DROP INDEX IF EXISTS correos_correo_asunto_trgm_idx;",
        ),
        _run_if_postgres(
            sql="""
                CREATE INDEX IF NOT EXISTS correos_correo_remitente_trgm_idx
                ON correos_correo USING GIN (remitente gin_trgm_ops);
            """,
            reverse_sql="DROP INDEX IF EXISTS correos_correo_remitente_trgm_idx;",
        ),
        _run_if_postgres(
            sql="""
                CREATE INDEX IF NOT EXISTS correos_correo_cuerpo_trgm_idx
                ON correos_correo USING GIN (cuerpo_texto gin_trgm_ops);
            """,
            reverse_sql="DROP INDEX IF EXISTS correos_correo_cuerpo_trgm_idx;",
        ),
    ]
