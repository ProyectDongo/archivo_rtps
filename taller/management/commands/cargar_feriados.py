"""
Carga feriados oficiales de Chile desde la API pública del gobierno
(https://apis.digital.gob.cl/fl/feriados — gratis, sin auth).

Uso:
    python manage.py cargar_feriados                    # año actual y siguiente
    python manage.py cargar_feriados --anio 2027         # un año específico
    python manage.py cargar_feriados --solo-faltantes    # no toca feriados ya cargados

Cron sugerido (1x por año):
    0 4 1 1 * docker exec <container> python manage.py cargar_feriados

NO borra los bloqueos manuales (`fuente=manual`). Solo crea/actualiza los
de fuente `api_gob`. Si querés "abrir" un feriado excepcionalmente, desmarcá
`activo` en admin sin borrar el registro.
"""
from __future__ import annotations

import json
import logging
import ssl
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from taller.models import BloqueoCalendario

logger = logging.getLogger('taller.cargar_feriados')


API_BASE = 'https://apis.digital.gob.cl/fl/feriados'


def _ssl_context_no_verify():
    """
    apis.digital.gob.cl históricamente tiene problemas con su cadena TLS
    (certs vencidos, intermediates rotos). Los feriados son datos PÚBLICOS
    sin riesgo de tampering — un atacante MitM podría a lo sumo alterar la
    lista de feriados, lo cual el admin puede corregir manualmente. Por
    eso aceptamos saltearnos verificación si la verificación normal falla.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_feriados_anio(anio: int) -> list[dict]:
    """
    Devuelve lista de feriados del año en el formato que devuelve la API:
      [{'fecha': 'YYYY-MM-DD', 'nombre': '...', 'tipo': '...', ...}, ...]

    Intenta con verificación TLS estricta primero; si la cadena del server
    está rota, reintenta sin verificación (ver `_ssl_context_no_verify`).
    """
    url = f'{API_BASE}/{anio}'
    req = Request(url, headers={
        'Accept': 'application/json',
        'User-Agent': 'PietramonteArchivo/1.0',
    })

    # 1er intento: TLS normal
    try:
        with urlopen(req, timeout=10) as resp:
            raw = resp.read().decode('utf-8')
    except URLError as e:
        msg = str(e).lower()
        if 'certificate' in msg or 'ssl' in msg or 'expired' in msg:
            logger.warning(
                'TLS verification failed para %s — reintento sin verify '
                '(API gob.cl sirve datos públicos): %s', url, e,
            )
            try:
                with urlopen(req, timeout=10, context=_ssl_context_no_verify()) as resp:
                    raw = resp.read().decode('utf-8')
            except URLError as e2:
                raise CommandError(f'Error consultando API gob.cl (también sin verify): {e2}')
        else:
            raise CommandError(f'Error consultando API gob.cl: {e}')

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise CommandError('Respuesta inválida de API gob.cl')

    if not isinstance(data, list):
        raise CommandError('La API devolvió un formato inesperado')
    return data


class Command(BaseCommand):
    help = 'Carga feriados oficiales de Chile desde apis.digital.gob.cl.'

    def add_arguments(self, parser):
        parser.add_argument('--anio', type=int, default=None,
                            help='Año a cargar. Si se omite, carga año actual + siguiente.')
        parser.add_argument('--solo-faltantes', action='store_true',
                            help='No actualizar feriados ya cargados — solo agregar los nuevos.')

    @transaction.atomic
    def handle(self, *args, **options):
        anios = []
        if options['anio']:
            anios = [options['anio']]
        else:
            actual = timezone.localdate().year
            anios = [actual, actual + 1]

        creados, actualizados = 0, 0
        for anio in anios:
            self.stdout.write(f'\n— Año {anio} —')
            try:
                feriados = _fetch_feriados_anio(anio)
            except CommandError as e:
                self.stderr.write(self.style.ERROR(str(e)))
                continue

            for f in feriados:
                fecha_str = f.get('fecha') or ''
                nombre    = (f.get('nombre') or 'Feriado nacional').strip()
                try:
                    fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
                except ValueError:
                    continue

                obj, creado = BloqueoCalendario.objects.get_or_create(
                    fecha=fecha,
                    defaults={
                        'motivo': nombre,
                        'fuente': BloqueoCalendario.Fuente.API_GOB,
                        'activo': True,
                    },
                )
                if creado:
                    creados += 1
                    self.stdout.write(f'  + {fecha} · {nombre}')
                elif not options['solo_faltantes']:
                    # Sólo actualiza si la fuente ya era api_gob (no pisar manuales)
                    if obj.fuente == BloqueoCalendario.Fuente.API_GOB and obj.motivo != nombre:
                        obj.motivo = nombre
                        obj.save(update_fields=['motivo'])
                        actualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nCarga completada: {creados} nuevos, {actualizados} actualizados.'
        ))
