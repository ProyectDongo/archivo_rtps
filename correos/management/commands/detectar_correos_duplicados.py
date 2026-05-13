"""
Detecta (y opcionalmente elimina) correos duplicados por (buzon, mensaje_id).

Diseñado para correr ANTES de aplicar la migración 0022, que agrega un
UNIQUE constraint partial sobre (buzon, mensaje_id). Si hay duplicados
existentes, la migración FALLA al crear la constraint y rollback.

Uso:

    # 1. Dry-run: lista cuántos hay sin tocar nada
    python manage.py detectar_correos_duplicados

    # 2. Eliminar duplicados (conserva el de menor id de cada grupo)
    python manage.py detectar_correos_duplicados --eliminar

    # 3. Conservar el más reciente en vez del más viejo
    python manage.py detectar_correos_duplicados --eliminar --conservar=mas-reciente

Seguridad:
- Considera "duplicado" solo cuando mensaje_id != '' (los vacíos no se
  pueden dedup confiablemente).
- Conserva uno de cada grupo (default: el de menor id = más viejo, que
  probablemente tiene los CorreoLeido/CorreoSnooze/Etiquetas asociados).
- Cuando borra un Correo, Django CASCADE borra automáticamente sus
  Adjunto, CorreoLeido, CorreoSnooze, etc. Por eso es destructivo —
  hacé pg_dump antes si tenés dudas.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min

from correos.models import Correo


class Command(BaseCommand):
    help = 'Detecta correos duplicados por (buzon, mensaje_id) antes de la migración 0022.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--eliminar', action='store_true',
            help='Borra los duplicados (deja uno de cada grupo). DESTRUCTIVO.',
        )
        parser.add_argument(
            '--conservar', choices=['mas-viejo', 'mas-reciente'],
            default='mas-viejo',
            help='Cuál de cada grupo de duplicados se conserva. Default: mas-viejo '
                 '(menor id, probablemente con más metadata asociada).',
        )
        parser.add_argument(
            '--mostrar', type=int, default=20,
            help='Cuántos grupos de duplicados mostrar en pantalla (default 20).',
        )

    def handle(self, *args, **opts):
        eliminar = opts['eliminar']
        conservar = opts['conservar']
        mostrar = opts['mostrar']

        # Grupos (buzon, mensaje_id) con más de un Correo (excluyendo mensaje_id vacío)
        grupos = (
            Correo.objects
            .exclude(mensaje_id='')
            .values('buzon_id', 'mensaje_id')
            .annotate(n=Count('id'), min_id=Min('id'), max_id=Max('id'))
            .filter(n__gt=1)
            .order_by('-n')
        )

        total_grupos = grupos.count()
        if total_grupos == 0:
            self.stdout.write(self.style.SUCCESS(
                '✓ No hay correos duplicados. La migración 0022 va a aplicar limpia.'
            ))
            return

        total_dupes = sum(g['n'] - 1 for g in grupos)  # exceso a borrar
        self.stdout.write(self.style.WARNING(
            f'Encontrados {total_grupos} grupos de duplicados '
            f'({total_dupes} correos en exceso a eliminar).'
        ))
        self.stdout.write('')

        self.stdout.write(f'Primeros {min(mostrar, total_grupos)} grupos:')
        for g in grupos[:mostrar]:
            self.stdout.write(
                f'  buzon_id={g["buzon_id"]}  msgid={g["mensaje_id"][:60]!r}  '
                f'n={g["n"]}  ids={g["min_id"]}..{g["max_id"]}'
            )

        if total_grupos > mostrar:
            self.stdout.write(f'  ... y {total_grupos - mostrar} grupos más.')

        if not eliminar:
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE(
                'Modo dry-run. Volver a correr con --eliminar para borrarlos.\n'
                'IMPORTANTE: hacé pg_dump primero. CASCADE borra Adjuntos / '
                'CorreoLeido / CorreoSnooze / etc asociados.'
            ))
            return

        # ─── Borrado real ─────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            f'Borrando {total_dupes} correos duplicados (conservando {conservar})…'
        ))

        borrados = 0
        for g in grupos:
            ids_grupo = list(
                Correo.objects
                .filter(buzon_id=g['buzon_id'], mensaje_id=g['mensaje_id'])
                .order_by('id')
                .values_list('id', flat=True)
            )
            if len(ids_grupo) <= 1:
                continue
            if conservar == 'mas-viejo':
                a_borrar = ids_grupo[1:]
            else:
                a_borrar = ids_grupo[:-1]
            n, _ = Correo.objects.filter(id__in=a_borrar).delete()
            borrados += len(a_borrar)

        self.stdout.write(self.style.SUCCESS(
            f'✓ Borrados {borrados} correos duplicados. '
            'Ahora ya podés aplicar la migración 0022.'
        ))
