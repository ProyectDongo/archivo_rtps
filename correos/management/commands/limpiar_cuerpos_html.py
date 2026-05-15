"""
Limpia correos cuyo `cuerpo_texto` contiene HTML literal — bug histórico
del save de los correos enviados desde el portal (HTML del editor Quill
pegado en el campo equivocado).

Para cada Correo afectado:
  - Si `cuerpo_html` está vacío → mueve el HTML a `cuerpo_html`, strippea
    `cuerpo_texto` (deja texto legible para snippet/búsqueda).
  - Si `cuerpo_html` ya tiene contenido → solo strippea `cuerpo_texto`
    (significa que ambos campos tenían el mismo HTML; limpiamos texto).

Uso:

    python manage.py limpiar_cuerpos_html --dry-run    # ver qué cambiaría
    python manage.py limpiar_cuerpos_html              # aplicar
    python manage.py limpiar_cuerpos_html --batch=200  # batch size custom

Idempotente: re-ejecutar no hace nada porque después del fix `cuerpo_texto`
ya no contiene tags HTML.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from correos.models import Correo
from correos.templatetags.correos_tags import (
    _texto_parece_html,
    html_a_texto,
)


class Command(BaseCommand):
    help = 'Limpia HTML pegado en cuerpo_texto de correos existentes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Solo cuenta los correos afectados, no toca la DB.',
        )
        parser.add_argument(
            '--batch', type=int, default=500,
            help='Tamaño del batch al actualizar (default 500).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch = options['batch']

        qs = Correo.objects.exclude(cuerpo_texto='').only(
            'id', 'cuerpo_texto', 'cuerpo_html'
        )
        total = qs.count()
        self.stdout.write(f'Analizando {total} correos con cuerpo_texto no vacío...')

        # Recorrer en batches para no cargar todo a RAM.
        candidatos: list[Correo] = []
        afectados = 0
        movidos_a_html = 0
        solo_strip = 0

        for c in qs.iterator(chunk_size=batch):
            if not _texto_parece_html(c.cuerpo_texto):
                continue
            afectados += 1

            texto_limpio = html_a_texto(c.cuerpo_texto)
            if not c.cuerpo_html:
                # cuerpo_html vacío: el HTML original solo vive en cuerpo_texto.
                # Movemos a cuerpo_html y dejamos texto strippeado.
                c.cuerpo_html = c.cuerpo_texto
                c.cuerpo_texto = texto_limpio
                movidos_a_html += 1
            else:
                # Ambos tenían HTML — solo strippeamos texto.
                c.cuerpo_texto = texto_limpio
                solo_strip += 1
            candidatos.append(c)

        self.stdout.write(self.style.WARNING(
            f'\nCorreos afectados: {afectados}'
        ))
        self.stdout.write(f'  - HTML movido a cuerpo_html: {movidos_a_html}')
        self.stdout.write(f'  - Solo strip de cuerpo_texto: {solo_strip}')

        if dry_run:
            self.stdout.write(self.style.NOTICE(
                '\nDry-run: no se actualizó nada. Re-corré sin --dry-run para aplicar.'
            ))
            return

        if not candidatos:
            self.stdout.write(self.style.SUCCESS('Nada que actualizar.'))
            return

        with transaction.atomic():
            Correo.objects.bulk_update(
                candidatos,
                ['cuerpo_texto', 'cuerpo_html'],
                batch_size=batch,
            )

        self.stdout.write(self.style.SUCCESS(
            f'\nActualizados {len(candidatos)} correos. Listo.'
        ))
