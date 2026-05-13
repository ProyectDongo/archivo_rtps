"""
Manda los emails de recordatorio de reservas activas:

  - 24h antes: ventana entre 23h y 25h antes de la cita (rangos amplios para
    cubrir desfases si el cron corre cada 5 min y se atrasa).
  - 1h antes:  ventana entre 30min y 90min antes de la cita.

Cada reserva guarda timestamps `reminder_24h_enviado_en` / `reminder_1h_enviado_en`
para no enviar duplicados.

También limpia reservas `pendiente_email` que llevan más de 30 min sin verificar:
las pasa a `cancelada_cliente` con motivo "no verificó email" para liberar el slot.

Uso:
    python manage.py enviar_recordatorios               # produce real
    python manage.py enviar_recordatorios --dry-run     # solo loggea
    python manage.py enviar_recordatorios --cleanup-only

Cron sugerido (cada 5 minutos):
    */5 * * * * docker exec <container> python manage.py enviar_recordatorios
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from taller.models import Reserva


PENDIENTE_EMAIL_TTL_MIN = 30      # pendientes > 30 min se cancelan solos


class Command(BaseCommand):
    help = 'Manda recordatorios 24h/1h y limpia reservas pendientes vencidas.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Solo muestra qué se mandaría, sin tocar BD ni mandar emails.')
        parser.add_argument('--cleanup-only', action='store_true',
                            help='Solo corre la limpieza de pendientes vencidas, sin mandar reminders.')

    def handle(self, *args, **options):
        from archivo_pietramonte.email_utils import safe_send

        dry            = options['dry_run']
        cleanup_only   = options['cleanup_only']
        ahora          = timezone.now()
        prefijo_dry    = '[DRY] ' if dry else ''

        # ─── Cleanup: pendientes vencidas ──────────────────────────────
        umbral = ahora - timedelta(minutes=PENDIENTE_EMAIL_TTL_MIN)
        qs_pend = Reserva.objects.filter(
            estado=Reserva.Estado.PENDIENTE_EMAIL,
            creada_en__lt=umbral,
        )
        n_pend = qs_pend.count()
        self.stdout.write(f'{prefijo_dry}Pendientes vencidas (>{PENDIENTE_EMAIL_TTL_MIN}min sin verificar): {n_pend}')
        if not dry and n_pend:
            with transaction.atomic():
                qs_pend.update(
                    estado=Reserva.Estado.CANCELADA_CLIENTE,
                    cancelada_en=ahora,
                    cancelada_por='sistema',
                    cancelada_motivo='No verificó email dentro del plazo',
                )

        if cleanup_only:
            self.stdout.write(self.style.SUCCESS('Solo cleanup. Listo.'))
            return

        # Estados de reserva activa
        ESTADOS_ACTIVOS = [
            Reserva.Estado.CONFIRMADA_EMAIL,
            Reserva.Estado.CONFIRMADA_LLAMADA,
        ]

        # ─── Reminders 24h ─────────────────────────────────────────────
        v24_ini = ahora + timedelta(hours=23)
        v24_fin = ahora + timedelta(hours=25)
        qs_24 = self._reservas_en_ventana(ESTADOS_ACTIVOS, v24_ini, v24_fin) \
            .filter(reminder_24h_enviado_en__isnull=True)
        n24 = qs_24.count()
        self.stdout.write(f'{prefijo_dry}Reminders 24h a mandar: {n24}')

        for r in qs_24:
            self._mandar_reminder(r, '24h', dry, safe_send, settings)

        # ─── Reminders 1h ──────────────────────────────────────────────
        v1_ini = ahora + timedelta(minutes=30)
        v1_fin = ahora + timedelta(minutes=90)
        qs_1 = self._reservas_en_ventana(ESTADOS_ACTIVOS, v1_ini, v1_fin) \
            .filter(reminder_1h_enviado_en__isnull=True)
        n1 = qs_1.count()
        self.stdout.write(f'{prefijo_dry}Reminders 1h a mandar: {n1}')

        for r in qs_1:
            self._mandar_reminder(r, '1h', dry, safe_send, settings)

        self.stdout.write(self.style.SUCCESS(
            f'\n{prefijo_dry}Resumen: pendientes canceladas={n_pend}, reminders 24h={n24}, reminders 1h={n1}'
        ))

    def _reservas_en_ventana(self, estados, ini, fin):
        """
        Reservas cuyo datetime de cita (fecha + hora_inicio) cae entre `ini` y `fin`.
        Hacemos el filtro fino en Python — el volumen es chico (max ~20 reservas/día)
        y evita SQL específico por motor.
        """
        # Pre-filtra por rango de fechas para no traer todo
        candidatas = Reserva.objects.filter(
            estado__in=estados,
            fecha__gte=ini.date(),
            fecha__lte=fin.date(),
        )
        # Filtra fino por datetime combinado
        ids_ok = []
        tz = timezone.get_current_timezone()
        for r in candidatas:
            dt = timezone.make_aware(datetime.combine(r.fecha, r.hora_inicio), tz)
            if ini <= dt <= fin:
                ids_ok.append(r.id)
        return Reserva.objects.filter(id__in=ids_ok)

    def _mandar_reminder(self, r, tipo: str, dry: bool, safe_send, settings_obj):
        prefijo_dry = '[DRY] ' if dry else ''
        self.stdout.write(f'  {prefijo_dry}→ Reminder {tipo}: {r.id} {r.cliente_email} {r.fecha} {r.hora_inicio}')

        if dry:
            return

        template = f'taller/email/reminder_{tipo}'
        asunto = (
            f'Recordatorio: tu cita en Pietramonte mañana a las {r.hora_inicio:%H:%M}'
            if tipo == '24h' else
            f'Tu cita en Pietramonte es en 1 hora ({r.hora_inicio:%H:%M})'
        )

        from taller.models import hash_token
        # Buscamos el token plano? No lo tenemos — solo el hash. El cliente
        # ya lo recibió en su email original. Acá mandamos el link a la URL
        # /agendar/r/<token>/ usando el hash NO sirve.
        #
        # Trick: mandamos el link sin token (a la home) y el cliente que
        # quiere ver/confirmar usa el link que ya tiene. Como mejora futura,
        # podemos guardar el token plano en sesión o cache para reusarlo.
        # Por simplicidad de Commit H, el reminder solo informa; los links
        # confirmar/cancelar quedan en el email original de confirmación.

        result = safe_send(
            asunto=asunto,
            para=r.cliente_email,
            template=template,
            contexto={'reserva': r, 'tipo_reminder': tipo},
            from_alias=getattr(settings_obj, 'EMAIL_AGENDA_FROM', None),
            reply_to=[settings_obj.EMAIL_REPLY_TO_AGENDA] if settings_obj.EMAIL_REPLY_TO_AGENDA else None,
        )

        if result['ok']:
            campo = 'reminder_24h_enviado_en' if tipo == '24h' else 'reminder_1h_enviado_en'
            setattr(r, campo, timezone.now())
            r.save(update_fields=[campo])
        else:
            self.stderr.write(self.style.WARNING(f'    Error: {result["error"]}'))
