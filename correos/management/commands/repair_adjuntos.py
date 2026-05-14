"""
Recupera archivos de adjuntos que existen en DB pero no en disco.

Sucede cuando el container se recrea sin volumen persistente para /app/data.
El comando:
  1. Busca todos los Adjunto con archivo inexistente en disco.
  2. Para cada correo afectado, re-fetcha el mensaje desde Gmail por Message-ID.
  3. Re-guarda los archivos en MEDIA_ROOT.

Uso:
    python manage.py repair_adjuntos
    python manage.py repair_adjuntos --dry-run   # solo reporta, no escribe
    python manage.py repair_adjuntos --label comercial  # solo un label
"""
from __future__ import annotations

import email as email_lib
import logging

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from correos.gmail_sync import ImapError, imap_connection
from correos.management.commands.import_mbox import extraer_adjuntos
from correos.models import Adjunto, BuzonGmailLabel

logger = logging.getLogger('correos.repair_adjuntos')


class Command(BaseCommand):
    help = 'Recupera archivos de adjuntos perdidos (en DB pero no en disco).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Solo reporta sin escribir nada.')
        parser.add_argument('--label', type=str, default=None,
                            help='Procesa solo este label de Gmail.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        solo_label = options['label']

        # Adjuntos sin archivo en disco
        adjuntos_rotos = []
        for adj in Adjunto.objects.select_related('correo__buzon').all():
            try:
                adj.archivo.open('rb').close()
            except (FileNotFoundError, ValueError):
                adjuntos_rotos.append(adj)

        if not adjuntos_rotos:
            self.stdout.write(self.style.SUCCESS('No hay adjuntos rotos. Todo OK.'))
            return

        self.stdout.write(f'Adjuntos sin archivo en disco: {len(adjuntos_rotos)}')

        # Agrupa por correo
        correos_con_rotos: dict[int, list[Adjunto]] = {}
        for adj in adjuntos_rotos:
            correos_con_rotos.setdefault(adj.correo_id, []).append(adj)

        self.stdout.write(f'Correos afectados: {len(correos_con_rotos)}')

        if dry_run:
            for cid, adjs in correos_con_rotos.items():
                adj = adjs[0]
                nombres = ', '.join(a.nombre_original for a in adjs)
                self.stdout.write(f'  correo_id={cid} msg_id={adj.correo.mensaje_id!r}: {nombres}')
            return

        # Necesitamos el label para saber en qué mailbox buscar
        labels_activos = BuzonGmailLabel.objects.select_related('buzon').all()
        if solo_label:
            labels_activos = labels_activos.filter(label_name=solo_label)

        reparados = 0
        no_encontrados = 0

        for sync in labels_activos:
            buzon = sync.buzon
            # correos de este buzón que tienen adjuntos rotos
            correos_buzon = {
                cid: adjs
                for cid, adjs in correos_con_rotos.items()
                if adjs[0].correo.buzon_id == buzon.id
            }
            if not correos_buzon:
                continue

            self.stdout.write(
                f'\n[{sync.label_name}] → {buzon.email}: '
                f'{len(correos_buzon)} correos con adjuntos rotos'
            )

            try:
                with imap_connection() as imap:
                    imap.select(f'"{sync.label_name}"', readonly=True)

                    for correo_id, adjs in correos_buzon.items():
                        correo = adjs[0].correo
                        msg_id = correo.mensaje_id
                        if not msg_id:
                            self.stdout.write(
                                f'  correo_id={correo_id}: sin mensaje_id, salteando'
                            )
                            no_encontrados += 1
                            continue

                        # Buscar UID por Message-ID
                        criterio = f'HEADER Message-ID "{msg_id}"'
                        try:
                            typ, data = imap.uid('search', None, criterio)
                        except Exception as e:
                            self.stdout.write(
                                self.style.ERROR(f'  Error buscando {msg_id}: {e}')
                            )
                            no_encontrados += 1
                            continue

                        uids = data[0].split() if data and data[0] else []
                        if not uids:
                            self.stdout.write(
                                f'  correo_id={correo_id}: no encontrado en Gmail'
                            )
                            no_encontrados += 1
                            continue

                        uid = uids[-1]
                        try:
                            typ2, msg_data = imap.uid('fetch', uid, '(RFC822)')
                            raw = msg_data[0][1]
                        except Exception as e:
                            self.stdout.write(
                                self.style.ERROR(f'  Error fetch uid={uid}: {e}')
                            )
                            no_encontrados += 1
                            continue

                        msg = email_lib.message_from_bytes(raw)
                        adjuntos_data = extraer_adjuntos(msg)

                        # Empareja por nombre de archivo
                        adj_por_nombre = {a.nombre_original: a for a in adjs}
                        for nombre, mime, payload, content_id in adjuntos_data:
                            if nombre in adj_por_nombre:
                                adj = adj_por_nombre[nombre]
                                adj.archivo.save(nombre, ContentFile(payload), save=True)
                                reparados += 1
                                self.stdout.write(
                                    self.style.SUCCESS(f'  OK: {nombre} ({len(payload)} bytes)')
                                )

            except ImapError as e:
                self.stdout.write(self.style.ERROR(f'  IMAP error: {e}'))

        self.stdout.write(
            f'\nReparados: {reparados}  |  No encontrados: {no_encontrados}'
        )
