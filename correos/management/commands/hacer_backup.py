"""
Backup de la base de datos Postgres a Backblaze B2 vía rclone + pg_dump.

Complementa backup_adjuntos_b2 (que cubre MEDIA_ROOT). Este command cubre la DB.

Cron sugerido (diario a las 3:15, 15 min después del backup de adjuntos):
    15 3 * * * docker exec $CONT python manage.py hacer_backup \
        >> /var/log/pietramonte-backup-db.log 2>&1

Env vars (mismas que backup_adjuntos_b2):
    B2_KEY_ID            keyID de la Application Key Backblaze
    B2_APPLICATION_KEY   applicationKey
    B2_BUCKET_NAME       nombre del bucket (ej. pietramonte-backups)

Uso manual:
    python manage.py hacer_backup              # backup real
    python manage.py hacer_backup --dry-run    # simula sin subir
"""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger('correos')

REMOTE_NAME = 'b2pm'
DB_PREFIX = 'db'


class Command(BaseCommand):
    help = 'Backup de Postgres → Backblaze B2 vía rclone + pg_dump.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Simula sin subir nada.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if not shutil.which('rclone'):
            raise CommandError(
                'rclone no está instalado. Verificá que el Dockerfile incluya rclone.'
            )
        if not shutil.which('pg_dump'):
            raise CommandError(
                'pg_dump no está instalado. Verificá que el Dockerfile incluya postgresql-client.'
            )

        key_id = os.environ.get('B2_KEY_ID', '').strip()
        app_key = os.environ.get('B2_APPLICATION_KEY', '').strip()
        bucket = os.environ.get('B2_BUCKET_NAME', '').strip()

        if not dry_run:
            missing = [n for n, v in [('B2_KEY_ID', key_id), ('B2_APPLICATION_KEY', app_key), ('B2_BUCKET_NAME', bucket)] if not v]
            if missing:
                raise CommandError(f"Faltan env vars: {', '.join(missing)}.")

        db_url = os.environ.get('DATABASE_URL', '')
        if not db_url or db_url.startswith('sqlite'):
            self.stdout.write('DATABASE_URL es SQLite o no está definida — nada que backupear.')
            return

        timestamp = datetime.now(timezone.utc).strftime('%Y/%m/%d/%H%M%S')
        dump_name = f'{timestamp}.sql.gz'

        with tempfile.TemporaryDirectory(prefix='db-backup-') as tmpdir:
            dump_path = Path(tmpdir) / dump_name

            self._dump_db(db_url, dump_path)

            if dry_run:
                size_kb = dump_path.stat().st_size // 1024
                self.stdout.write(f'[dry-run] Se subiría {dump_name} ({size_kb}kb) → {bucket}/{DB_PREFIX}/')
                return

            config_path = self._write_rclone_config(tmpdir, key_id, app_key)
            env = {**os.environ, 'RCLONE_CONFIG': config_path}
            self._upload(env, bucket, dump_path, dump_name)

        self.stdout.write(self.style.SUCCESS('DB backup OK.'))

    def _dump_db(self, db_url: str, dest: Path) -> None:
        self.stdout.write('Ejecutando pg_dump...')
        result = subprocess.run(
            ['pg_dump', '--no-password', '--clean', '--if-exists', db_url],
            capture_output=True,
        )
        if result.returncode != 0:
            raise CommandError(f'pg_dump falló: {result.stderr.decode()[:500]}')

        dest.write_bytes(gzip.compress(result.stdout))
        size_kb = dest.stat().st_size // 1024
        self.stdout.write(f'Dump generado: {size_kb}kb comprimido.')
        logger.info('pg_dump ok: %skb', size_kb)

    def _write_rclone_config(self, tmpdir: str, key_id: str, app_key: str) -> str:
        config_path = os.path.join(tmpdir, 'rclone.conf')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(
                f'[{REMOTE_NAME}]\n'
                f'type = b2\n'
                f'account = {key_id}\n'
                f'key = {app_key}\n'
                f'hard_delete = false\n'
            )
        os.chmod(config_path, 0o600)
        return config_path

    def _upload(self, env: dict, bucket: str, dump_path: Path, dump_name: str) -> None:
        dest = f'{REMOTE_NAME}:{bucket}/{DB_PREFIX}/{dump_name}'
        self.stdout.write(f'Subiendo a {dest}...')
        proc = subprocess.run(
            ['rclone', 'copyto', str(dump_path), dest, '--log-level', 'INFO'],
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise CommandError(f'rclone copyto falló: {proc.stderr[:400]}')
        logger.info('backup_db subido: %s', dest)
