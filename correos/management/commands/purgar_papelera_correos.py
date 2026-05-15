"""
Auto-purga de la papelera de correos.

Marca como `purgado=True` todos los `CorreoEliminado` con `purgado=False`
y `eliminado_en` más antiguo que N días (default 30). Es la equivalencia
de "Gmail elimina automáticamente los correos en papelera tras 30 días".

Idempotente: re-correr no hace daño (ya marcados como purgado=True no
califican para el update).

Uso (idealmente por cron diario):

    python manage.py purgar_papelera_correos --dry-run
    python manage.py purgar_papelera_correos
    python manage.py purgar_papelera_correos --dias=60   # margen extendido
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from correos.models import CorreoEliminado


class Command(BaseCommand):
    help = 'Auto-purga correos en papelera mas viejos de N dias.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias', type=int, default=30,
            help='Cuántos días en papelera antes de purgar (default 30).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Solo cuenta los registros a purgar, no toca la DB.',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(days=dias)

        qs = CorreoEliminado.objects.filter(
            purgado=False, eliminado_en__lt=cutoff,
        )
        n = qs.count()
        self.stdout.write(f'En papelera con {dias}+ días: {n} registros.')

        if dry_run or n == 0:
            if dry_run:
                self.stdout.write(self.style.NOTICE(
                    'Dry-run: no se modificó nada.'
                ))
            return

        updated = qs.update(purgado=True)
        self.stdout.write(self.style.SUCCESS(
            f'Purgados {updated} correos (purgado=True). Listo.'
        ))
