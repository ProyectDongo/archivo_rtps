"""
Muestra el estado del sync por buzón Gmail.

Para cada BuzonGmailLabel:
  - Buzón al que está atado
  - Label de Gmail que sincroniza
  - last_uid (cursor IMAP)
  - last_sync_at (cuándo corrió por última vez)
  - correos_sincronizados (total acumulado)
  - error_msg (si el último intento falló)
  - cuántos correos hay en la DB para ese buzón ahora mismo
  - cuántos llegaron en las últimas 24h (señal de "está vivo")

Uso:
    python manage.py estado_sync           # todos los buzones
    python manage.py estado_sync --solo-errores
    python manage.py estado_sync --buzon cpietrasanta@pietramonte.cl
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from correos.models import BuzonGmailLabel, Correo


class Command(BaseCommand):
    help = 'Estado del sync por buzón Gmail — útil para diagnóstico operacional.'

    def add_arguments(self, parser):
        parser.add_argument('--solo-errores', action='store_true',
                            help='Solo mostrar buzones con error_msg no vacío.')
        parser.add_argument('--buzon', type=str,
                            help='Filtrar por email del buzón (ej. cpietrasanta@pietramonte.cl).')

    def handle(self, *args, **opts):
        ahora = timezone.now()
        hace_24h = ahora - timedelta(hours=24)

        qs = BuzonGmailLabel.objects.select_related('buzon').order_by('buzon__email', 'label_name')
        if opts.get('buzon'):
            qs = qs.filter(buzon__email__iexact=opts['buzon'])
        if opts['solo_errores']:
            qs = qs.exclude(error_msg='')

        if not qs.exists():
            self.stdout.write(self.style.WARNING('Sin BuzonGmailLabel que coincidan con el filtro.'))
            return

        for sync in qs:
            buzon = sync.buzon

            # Stats del buzón
            total_correos = buzon.correos.count()
            ultimas_24h = buzon.correos.filter(fecha__gte=hace_24h).count()
            ultima_fecha = buzon.correos.order_by('-fecha').values_list('fecha', flat=True).first()

            # Tiempo desde último sync
            if sync.last_sync_at:
                delta = ahora - sync.last_sync_at
                if delta.total_seconds() < 60:
                    hace_sync = f'{int(delta.total_seconds())}s'
                elif delta.total_seconds() < 3600:
                    hace_sync = f'{int(delta.total_seconds() / 60)}min'
                elif delta.total_seconds() < 86400:
                    hace_sync = f'{int(delta.total_seconds() / 3600)}h'
                else:
                    hace_sync = f'{delta.days}d'
            else:
                hace_sync = 'nunca'

            activo = '✓' if sync.activo else '✗'
            error_flag = self.style.ERROR('  [ERROR]') if sync.error_msg else ''

            self.stdout.write('')
            self.stdout.write(self.style.NOTICE(
                f'{activo} {buzon.email}  →  label "{sync.label_name}"{error_flag}'
            ))
            self.stdout.write(f'    last_uid        = {sync.last_uid}')
            self.stdout.write(f'    last_sync_at    = {sync.last_sync_at:%Y-%m-%d %H:%M:%S} ({hace_sync} atrás)'
                              if sync.last_sync_at else
                              f'    last_sync_at    = (nunca corrió)')
            self.stdout.write(f'    sincronizados   = {sync.correos_sincronizados} (acumulado histórico)')
            self.stdout.write(f'    correos en DB   = {total_correos}')
            self.stdout.write(f'    últimas 24h     = {ultimas_24h}')
            if ultima_fecha:
                self.stdout.write(f'    correo más reciente = {ultima_fecha:%Y-%m-%d %H:%M}')
            if sync.error_msg:
                self.stdout.write(self.style.ERROR(f'    error_msg       = {sync.error_msg[:200]}'))

        # Resumen final
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('─' * 60))

        total = qs.count()
        activos = qs.filter(activo=True).count()
        con_errores = qs.exclude(error_msg='').count()
        sin_correr = qs.filter(last_sync_at__isnull=True).count()
        ultimos_sync = qs.filter(last_sync_at__gte=ahora - timedelta(minutes=10)).count()

        self.stdout.write(f'Total mappings    : {total}')
        self.stdout.write(f'Activos           : {activos}')
        self.stdout.write(f'Con error último  : {con_errores}')
        self.stdout.write(f'Nunca corrieron   : {sin_correr}')
        self.stdout.write(f'Última corrida en <10min : {ultimos_sync}')

        # Última corrida global (sea qué buzón sea)
        ultima_global = (BuzonGmailLabel.objects
                         .filter(last_sync_at__isnull=False)
                         .order_by('-last_sync_at')
                         .values_list('last_sync_at', flat=True).first())
        if ultima_global:
            delta_global = ahora - ultima_global
            self.stdout.write(f'Última corrida global    : {ultima_global:%Y-%m-%d %H:%M:%S} '
                              f'({int(delta_global.total_seconds() / 60)}min atrás)')
