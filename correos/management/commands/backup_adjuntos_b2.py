"""
Backup incremental de /app/data/adjuntos a Backblaze B2 vía rclone.

Diseñado para correr por cron nocturno desde el HOST (Hetzner), igual que
`enviar_recordatorios` y `cargar_feriados` (ver DEPLOY.md §11.5):

    30 3 * * * docker exec $CONT python manage.py backup_adjuntos_b2 \
        >> /var/log/pietramonte-backup-adjuntos.log 2>&1

Estrategia: `rclone sync` con `--backup-dir` versionado por fecha.
- Archivos nuevos/modificados: se suben.
- Archivos borrados localmente: se mueven a `adjuntos-archive/AAAAMMDD/`
  en el bucket (soft-delete, recuperables N días — depende de tu
  política de retención del bucket).
- No se borra nada del lado local.

Env vars requeridas (configuradas en Coolify):
    B2_KEY_ID              keyID de la Application Key Backblaze
    B2_APPLICATION_KEY     applicationKey
    B2_BUCKET_NAME         nombre del bucket (ej. pietramonte-backups)
    B2_REGION              opcional, p.ej. us-west-002 (solo informativo)
    B2_ENDPOINT            opcional, solo si usás S3 endpoint en vez de B2 nativo

Uso manual:
    python manage.py backup_adjuntos_b2                  # sync real
    python manage.py backup_adjuntos_b2 --dry-run        # no sube nada, solo loguea
    python manage.py backup_adjuntos_b2 --check          # valida config + lista bucket
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


REMOTE_NAME = "b2pm"
DEST_PREFIX = "adjuntos"
ARCHIVE_PREFIX = "adjuntos-archive"


class Command(BaseCommand):
    help = "Sincroniza MEDIA_ROOT (adjuntos) a Backblaze B2 vía rclone."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="No transfiere; solo lista lo que haría.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Valida credenciales y lista el bucket. No sincroniza.",
        )
        parser.add_argument(
            "--bwlimit",
            default="10M",
            help="Límite de ancho de banda para rclone (default 10M=10MB/s).",
        )

    def handle(self, *args, **opts):
        if not shutil.which("rclone"):
            raise CommandError(
                "rclone no está instalado en el contenedor. "
                "Verificá que el Dockerfile incluya `apt-get install rclone`."
            )

        key_id = os.environ.get("B2_KEY_ID", "").strip()
        app_key = os.environ.get("B2_APPLICATION_KEY", "").strip()
        bucket = os.environ.get("B2_BUCKET_NAME", "").strip()

        missing = [
            name
            for name, val in [
                ("B2_KEY_ID", key_id),
                ("B2_APPLICATION_KEY", app_key),
                ("B2_BUCKET_NAME", bucket),
            ]
            if not val
        ]
        if missing:
            raise CommandError(
                f"Faltan env vars: {', '.join(missing)}. "
                "Configurarlas en Coolify → Environment Variables."
            )

        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists():
            raise CommandError(f"MEDIA_ROOT no existe: {media_root}")

        with tempfile.TemporaryDirectory(prefix="rclone-b2-") as tmpdir:
            config_path = self._write_rclone_config(tmpdir, key_id, app_key)
            env = {**os.environ, "RCLONE_CONFIG": config_path}

            if opts["check"]:
                self._run_check(env, bucket)
                return

            self._run_sync(env, bucket, media_root, opts["dry_run"], opts["bwlimit"])

    def _write_rclone_config(self, tmpdir: str, key_id: str, app_key: str) -> str:
        """Escribe un rclone.conf efímero. No queda en disco después del backup."""
        config_path = os.path.join(tmpdir, "rclone.conf")
        # Backend nativo b2 — más eficiente que el adaptador S3 para Backblaze
        # (usa la API de B2 directa; soporta chunked upload + sha1 hash check).
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                f"[{REMOTE_NAME}]\n"
                f"type = b2\n"
                f"account = {key_id}\n"
                f"key = {app_key}\n"
                f"hard_delete = false\n"  # soft-delete: usa versioning del bucket
            )
        os.chmod(config_path, 0o600)
        return config_path

    def _run_check(self, env: dict, bucket: str) -> None:
        self.stdout.write(self.style.NOTICE(f"[check] Listando {REMOTE_NAME}:{bucket}…"))
        proc = subprocess.run(
            ["rclone", "lsd", f"{REMOTE_NAME}:{bucket}"],
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self.stderr.write(proc.stderr)
            raise CommandError("rclone lsd falló — credenciales o bucket inválidos.")
        self.stdout.write(proc.stdout or "(bucket vacío)")
        self.stdout.write(self.style.SUCCESS("[check] OK — credenciales válidas."))

    def _run_sync(
        self,
        env: dict,
        bucket: str,
        media_root: Path,
        dry_run: bool,
        bwlimit: str,
    ) -> None:
        archive_dir = f"{REMOTE_NAME}:{bucket}/{ARCHIVE_PREFIX}/{datetime.now():%Y%m%d}"
        dest = f"{REMOTE_NAME}:{bucket}/{DEST_PREFIX}"

        cmd = [
            "rclone",
            "sync",
            str(media_root),
            dest,
            "--backup-dir", archive_dir,
            "--bwlimit", bwlimit,
            "--transfers", "4",
            "--checkers", "8",
            "--fast-list",
            "--stats", "30s",
            "--stats-one-line",
            "--log-level", "INFO",
        ]
        if dry_run:
            cmd.append("--dry-run")

        self.stdout.write(self.style.NOTICE(
            f"[sync] {media_root} → {dest} "
            f"(dry_run={dry_run}, archive_dir={archive_dir})"
        ))

        # Streamea stdout/stderr en tiempo real al log del cron.
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        proc.wait()

        if proc.returncode != 0:
            raise CommandError(f"rclone sync devolvió código {proc.returncode}")

        self.stdout.write(self.style.SUCCESS("[sync] OK"))
