"""
Inspecciona los campos clave de un correo guardado en la DB.

Útil cuando un correo se ve raro en el portal y queremos saber por qué.
Muestra cuerpo_texto y cuerpo_html en raw (con preview) + metadata para
diagnosticar problemas de render.

Uso:
    python manage.py inspeccionar_correo 12345           # por id
    python manage.py inspeccionar_correo 12345 --full    # imprime cuerpo completo
    python manage.py inspeccionar_correo --asunto "FACTURA COMERCIAL" --buzon 1
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from correos.models import Correo


class Command(BaseCommand):
    help = 'Inspecciona un correo guardado para diagnóstico de render.'

    def add_arguments(self, parser):
        parser.add_argument('correo_id', type=int, nargs='?', default=None,
                            help='ID del correo a inspeccionar.')
        parser.add_argument('--asunto', type=str,
                            help='Buscar por substring en asunto (case-insensitive).')
        parser.add_argument('--buzon', type=int,
                            help='Filtrar por id de buzón (combina con --asunto).')
        parser.add_argument('--full', action='store_true',
                            help='Imprimir cuerpo completo en vez de preview de 500 chars.')

    def handle(self, *args, **opts):
        if opts['correo_id']:
            try:
                correo = Correo.objects.select_related('buzon').get(id=opts['correo_id'])
            except Correo.DoesNotExist:
                raise CommandError(f'No existe Correo id={opts["correo_id"]}')
            self._mostrar(correo, opts['full'])
            return

        # Búsqueda por asunto
        if not opts['asunto']:
            raise CommandError('Pasar correo_id o --asunto.')

        qs = Correo.objects.select_related('buzon').filter(asunto__icontains=opts['asunto'])
        if opts['buzon']:
            qs = qs.filter(buzon_id=opts['buzon'])
        qs = qs.order_by('-fecha')[:5]

        if not qs:
            self.stdout.write(self.style.WARNING('Sin resultados.'))
            return

        n = qs.count() if hasattr(qs, 'count') else len(qs)
        self.stdout.write(self.style.NOTICE(
            f'Encontrados {n} correos (mostrando los primeros 5):'
        ))
        for c in qs:
            self.stdout.write(f'  id={c.id}  buzon={c.buzon.email}  '
                              f'fecha={c.fecha:%Y-%m-%d %H:%M if c.fecha else "—"}  '
                              f'asunto="{c.asunto[:60]}"')

        self.stdout.write('')
        self.stdout.write('Re-correr con: python manage.py inspeccionar_correo <id>')

    def _mostrar(self, correo: Correo, full: bool):
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('═' * 60))
        self.stdout.write(self.style.NOTICE(f'  Correo id={correo.id}'))
        self.stdout.write(self.style.NOTICE('═' * 60))

        self.stdout.write(f'Buzón         : {correo.buzon.email}  (id={correo.buzon_id})')
        self.stdout.write(f'Mensaje-ID    : {correo.mensaje_id}')
        self.stdout.write(f'Remitente     : {correo.remitente}')
        self.stdout.write(f'Destinatario  : {correo.destinatario[:200]}')
        self.stdout.write(f'Asunto        : {correo.asunto}')
        self.stdout.write(f'Fecha         : {correo.fecha}')
        self.stdout.write(f'Carpeta       : {correo.tipo_carpeta}')
        self.stdout.write(f'Tiene adjunto : {correo.tiene_adjunto}')

        adjuntos = list(correo.adjuntos.all())
        if adjuntos:
            self.stdout.write(f'Adjuntos ({len(adjuntos)}):')
            for a in adjuntos:
                cid = f' cid={a.content_id}' if a.content_id else ''
                self.stdout.write(f'  - {a.nombre_original} ({a.mime_type}, '
                                  f'{a.tamano_bytes} bytes){cid}')

        # ─── cuerpo_texto ─────────────────────────────────────────────────
        texto = correo.cuerpo_texto or ''
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE(f'── cuerpo_texto ({len(texto)} chars) ──'))
        if not texto:
            self.stdout.write(self.style.WARNING('  (vacío)'))
        else:
            # Detectar si el "texto" es realmente HTML disfrazado
            inicio = texto[:200].lower().lstrip()
            es_html_disfrazado = any(inicio.startswith(m) for m in [
                '<!doctype html', '<html', '<?xml', '<head', '<body', '<table',
            ])
            if es_html_disfrazado:
                self.stdout.write(self.style.WARNING(
                    '  ⚠️  El cuerpo_texto parece HTML disfrazado de texto plano. '
                    'El template debería renderizarlo como HTML via render_correo_body.'
                ))
            cuerpo = texto if full else texto[:500]
            self.stdout.write(cuerpo)
            if not full and len(texto) > 500:
                self.stdout.write(self.style.NOTICE(f'  ... [{len(texto) - 500} chars más, usar --full]'))

        # ─── cuerpo_html ──────────────────────────────────────────────────
        html = correo.cuerpo_html or ''
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE(f'── cuerpo_html ({len(html)} chars) ──'))
        if not html:
            self.stdout.write(self.style.WARNING(
                '  (vacío) — el extractor del sync no encontró parte text/html '
                'o no la guardó. Por eso el template cae al cuerpo_texto.'
            ))
        else:
            cuerpo = html if full else html[:500]
            self.stdout.write(cuerpo)
            if not full and len(html) > 500:
                self.stdout.write(self.style.NOTICE(f'  ... [{len(html) - 500} chars más, usar --full]'))

        self.stdout.write('')
