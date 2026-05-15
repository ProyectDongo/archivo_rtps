"""
Backfill de `Correo.thread` para correos viejos que no tienen el FK seteado,
o re-construcción total con `--reset`.

Algoritmo (en orden de preferencia):

1. **Por headers MIME**: si el Correo tiene `in_reply_to` o `references`
   poblados, se busca un Correo padre con `mensaje_id` matcheando y se
   hereda su thread.

2. **Por asunto (OPT-IN)**: solo si pasás `--legacy-subject-fallback`. Útil
   para correos legacy importados desde mbox sin headers. Requiere proximidad
   temporal (default 30 días) para evitar falsos positivos del estilo
   "20 correos con mismo asunto pero conversaciones distintas".

Sin `--legacy-subject-fallback`, los correos sin headers MIME quedan cada
uno en su propio thread (1 mensaje). Es el comportamiento estricto que evita
agrupaciones incorrectas, mismo criterio que usa Gmail.

Recorre los Correos por fecha ASC — así el más antiguo de cada hilo queda
como raíz natural.

Uso:

    # Backfill normal: solo correos sin thread, strict
    python manage.py recompute_threads --dry-run
    python manage.py recompute_threads

    # Rebuild total: NULLifica todos los thread_id, borra Threads vacíos,
    # reconstruye desde cero. Útil tras cambiar las reglas (como ahora).
    python manage.py recompute_threads --reset

    # Modo legacy: usa fallback por asunto + proximidad temporal
    python manage.py recompute_threads --reset --legacy-subject-fallback

    # Solo un buzón
    python manage.py recompute_threads --reset --buzon=1
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
        parser.add_argument('--reset', action='store_true',
                            help='Destructivo: nullifica TODOS los Correo.thread, borra '
                                 'Threads vacios, y reconstruye desde cero. Usar tras '
                                 'cambios en las reglas de threading.')
        parser.add_argument('--legacy-subject-fallback', action='store_true',
                            help='Habilita fallback por asunto + proximidad temporal para '
                                 'correos sin headers MIME. Riesgo: agrupa correos con '
                                 'asuntos identicos pero conversaciones distintas.')
        parser.add_argument('--subject-dias', type=int, default=30,
                            help='Si --legacy-subject-fallback, ventana en dias para '
                                 'considerar dos correos del mismo hilo (default 30).')
        parser.add_argument('--buzon', type=int, default=None,
                            help='Limita el backfill a un buzon_id.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        reset   = options['reset']
        subject_fb = options['legacy_subject_fallback']
        subject_dias = options['subject_dias']
        buzon_id = options['buzon']

        buzones_qs = Buzon.objects.all()
        if buzon_id:
            buzones_qs = buzones_qs.filter(id=buzon_id)

        # --reset: nullifica todos los thread_id y borra Threads del/los buzones.
        if reset:
            self.stdout.write(self.style.WARNING(
                '--reset: nullificando Correo.thread y borrando Threads existentes...'
            ))
            if not dry_run:
                with transaction.atomic():
                    for buzon in buzones_qs:
                        n_correos = Correo.objects.filter(buzon=buzon).update(thread=None)
                        n_threads = Thread.objects.filter(buzon=buzon).count()
                        Thread.objects.filter(buzon=buzon).delete()
                        self.stdout.write(
                            f'  {buzon.email}: {n_correos} correos sin thread, '
                            f'{n_threads} Threads borrados.'
                        )

        total_correos = 0
        total_asignados = 0
        total_threads_creados = 0

        for buzon in buzones_qs:
            self.stdout.write(f'\n→ Buzón: {buzon.email}')

            qs = Correo.objects.filter(buzon=buzon)
            if not reset:
                # Modo backfill: solo los que no tienen thread aun.
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
                    fecha=c.fecha,
                    subject_fallback=subject_fb,
                    subject_fallback_dias=subject_dias,
                )
                if parent_thread is None:
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

            # Recompute cache de TODOS los threads del buzón.
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
