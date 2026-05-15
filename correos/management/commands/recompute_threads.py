"""
Backfill de `Correo.thread` para correos viejos que no tienen el FK seteado.

Algoritmo (en orden de preferencia):

1. **Por headers**: si el Correo tiene `in_reply_to` o `references` poblados,
   se busca un Correo padre con `mensaje_id` matcheando y se hereda su thread.

2. **Por asunto normalizado + buzón**: si no hay match por headers, se busca
   un Thread existente con el mismo `asunto` normalizado en el mismo buzón
   y se asigna. Si no existe, se crea uno nuevo con el Correo más antiguo
   del grupo como raíz.

Recorre los Correos por fecha ascendente — así el más antiguo de cada hilo
queda como raíz natural.

Idempotente: re-ejecutar no rompe nada porque los Correos ya con thread se
omiten (a menos que pases --force).

Uso:

    python manage.py recompute_threads --dry-run     # preview
    python manage.py recompute_threads               # aplicar
    python manage.py recompute_threads --force       # recalcular TODO (reset)
    python manage.py recompute_threads --buzon=1     # solo un buzón
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from correos.models import Buzon, Correo, Thread
from correos.threading import (
    find_parent_thread,
    create_thread_for,
    normalizar_asunto,
    recompute_thread_cache,
)


class Command(BaseCommand):
    help = 'Reconstruye Correo.thread y cache de Thread para correos viejos.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='No toca la DB, solo cuenta.')
        parser.add_argument('--force', action='store_true',
                            help='Recalcula todos los correos, incluso los que ya tenian thread.')
        parser.add_argument('--buzon', type=int, default=None,
                            help='Limita el backfill a un buzon_id.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force   = options['force']
        buzon_id = options['buzon']

        buzones_qs = Buzon.objects.all()
        if buzon_id:
            buzones_qs = buzones_qs.filter(id=buzon_id)

        total_correos = 0
        total_asignados = 0
        total_threads_creados = 0

        for buzon in buzones_qs:
            self.stdout.write(f'\n→ Buzón: {buzon.email}')

            qs = Correo.objects.filter(buzon=buzon)
            if not force:
                qs = qs.filter(thread__isnull=True)
            # Iteramos por fecha ASC: el más antiguo de un hilo va primero
            # → naturalmente queda como raíz.
            qs = qs.order_by('fecha', 'id').only(
                'id', 'mensaje_id', 'in_reply_to', 'references',
                'asunto', 'fecha', 'buzon_id', 'thread_id',
            )

            asignados = 0
            threads_creados = 0
            count = qs.count()
            total_correos += count
            self.stdout.write(f'  correos a procesar: {count}')

            if count == 0:
                continue

            for c in qs.iterator(chunk_size=200):
                parent_thread = find_parent_thread(
                    buzon, c.in_reply_to, c.references, c.asunto,
                )
                if parent_thread is None:
                    # No matchea: este correo abre un Thread nuevo.
                    if dry_run:
                        threads_creados += 1
                        continue
                    parent_thread = create_thread_for(c)
                    threads_creados += 1
                else:
                    if dry_run:
                        asignados += 1
                        continue

                c.thread = parent_thread
                Correo.objects.filter(id=c.id).update(thread=parent_thread)
                asignados += 1

            # Recompute cache de TODOS los threads tocados en este buzón.
            if not dry_run:
                buzon_threads = Thread.objects.filter(buzon=buzon)
                for t in buzon_threads:
                    recompute_thread_cache(t)

            self.stdout.write(self.style.SUCCESS(
                f'  asignados={asignados}, threads_nuevos={threads_creados}'
            ))
            total_asignados += asignados
            total_threads_creados += threads_creados

        self.stdout.write('\n──────────────────────────────────')
        self.stdout.write(self.style.SUCCESS(
            f'Total: {total_correos} correos analizados · '
            f'{total_asignados} asignados · {total_threads_creados} threads creados'
        ))
        if dry_run:
            self.stdout.write(self.style.NOTICE(
                'Dry-run: no se modificó nada. Re-corré sin --dry-run.'
            ))
