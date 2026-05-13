"""
Sincroniza correos NUEVOS desde Gmail al archivo, por label.

Diseñado para correr por cron cada 5-15 min:
    */10 * * * * docker exec $CONT python manage.py sincronizar_gmail

Setup:
  1. Configurar EMAIL_HOST_USER + EMAIL_HOST_PASSWORD en Coolify env vars
     (la cuenta Gmail centralizadora + su App Password).
  2. Crear los `BuzonGmailLabel` desde el admin (label_name → buzón).
     Para ver labels disponibles: `python manage.py sincronizar_gmail --listar-labels`.
  3. Primera corrida con last_uid=0 trae TODA la historia del label.
     Después solo entra lo nuevo.

Uso manual:
    sincronizar_gmail                       # Sincroniza todos los labels activos.
    sincronizar_gmail --label "aledezma"    # Solo este label.
    sincronizar_gmail --listar-labels       # Lista labels Gmail y sale.
    sincronizar_gmail --reset-uid --label X # last_uid=0 → re-fetch todo.

Dedup por mensaje_id está garantizado: si el cron corre 2 veces o si el
mismo correo aparece en varios labels, no se duplica.
"""
from __future__ import annotations

import email as email_lib
import email.utils
import logging
import time

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from correos.gmail_sync import ImapError, OverquotaError, fetch_nuevos, listar_labels
from correos.models import Adjunto, BuzonGmailLabel, Correo


# Lock anti-solapamiento: si una corrida de sync se demora más que el
# intervalo del cron, la siguiente debe salir limpia sin hacer nada en vez
# de competir con la anterior (que podría provocar duplicación de inserts
# entre el chequeo en memoria y el INSERT).
SYNC_LOCK_KEY = 'sync_gmail:running'
SYNC_LOCK_TTL = 30 * 60       # 30 min — si por algún motivo el lock no se
                              # libera (crash), se cae solo y la siguiente
                              # corrida vuelve a tomar el control.

# Flag de pausa por OVERQUOTA de Gmail. Cuando Gmail rate-limitea la cuenta
# por exceso de uso IMAP (típico tras OOM-kills que dejan conexiones
# half-open), seteamos este flag para que los próximos ticks del cron salgan
# limpios en vez de seguir golpeando y empeorar el bloqueo.
# Gmail desbloquea solo en 12-24h.
OVERQUOTA_PAUSE_KEY = 'sync_gmail:overquota_until'
OVERQUOTA_PAUSE_SEG = 24 * 60 * 60   # 24h
from correos.management.commands.import_mbox import (
    decodificar_header,
    extraer_adjuntos,
    extraer_cuerpos,
)


logger = logging.getLogger('correos.sincronizar_gmail')


class Command(BaseCommand):
    help = 'Sincroniza correos nuevos desde Gmail vía IMAP, por label → buzón.'

    def add_arguments(self, parser):
        parser.add_argument('--label', type=str,
                            help='Sincronizar solo este label_name (string exacto).')
        parser.add_argument('--listar-labels', dest='listar_labels', action='store_true',
                            help='Lista los labels disponibles en Gmail y sale (no sincroniza).')
        parser.add_argument('--reset-uid', dest='reset_uid', action='store_true',
                            help='Pone last_uid=0 antes de sincronizar (re-fetch toda la historia). '
                                 'Combinar con --label para no resetear todo.')
        parser.add_argument('--quiet', action='store_true',
                            help='Silencia el output rutinario (útil para cron sin spam).')
        parser.add_argument('--ignore-lock', action='store_true',
                            help='Forzar ejecución aunque haya otro sync corriendo. '
                                 'Solo para diagnóstico — peligroso en cron.')
        parser.add_argument('--ignore-overquota', action='store_true',
                            help='Forzar ejecución aunque Gmail haya bloqueado por '
                                 'OVERQUOTA en las últimas 24h. PELIGROSO: cada intento '
                                 'extiende el bloqueo. Solo si estás 100% seguro que '
                                 'Gmail ya desbloqueó.')
        parser.add_argument('--max-labels', type=int, default=5,
                            help='Máximo de labels a sincronizar por corrida (default 5). '
                                 'Ordena por last_sync_at ASC NULLS FIRST → los nunca-corridos '
                                 'y los más viejos van primero. Garantiza fairness: con cron */2 '
                                 'y 20 labels, una vuelta completa toma ~8 min.')

    def handle(self, *args, **options):
        # ─── Pausa por OVERQUOTA de Gmail ──────────────────────────────────
        # Si en una corrida anterior Gmail nos rate-limiteó, salimos limpios
        # durante 24h en vez de seguir golpeando (cada intento extiende el
        # bloqueo). El --label individual también queda pausado, salvo que
        # el operador pase --ignore-overquota explícitamente.
        overquota_until = cache.get(OVERQUOTA_PAUSE_KEY)
        if overquota_until and not options.get('ignore_overquota'):
            from datetime import datetime
            try:
                ts = datetime.fromtimestamp(int(overquota_until))
                ts_str = ts.strftime('%Y-%m-%d %H:%M:%S')
            except (TypeError, ValueError):
                ts_str = '(desconocido)'
            if not options['quiet']:
                self.stdout.write(self.style.WARNING(
                    f'⚠️  Gmail OVERQUOTA — sync pausado hasta ~{ts_str}. '
                    f'Salgo limpio. Pasá --ignore-overquota si Gmail ya desbloqueó.'
                ))
            return

        if options['listar_labels']:
            try:
                for lab in sorted(listar_labels()):
                    self.stdout.write(f'  {lab}')
            except ImapError as e:
                self.stderr.write(self.style.ERROR(f'IMAP error: {e}'))
            return

        qs = BuzonGmailLabel.objects.filter(activo=True).select_related('buzon')
        if options.get('label'):
            qs = qs.filter(label_name=options['label'])

        if options.get('reset_uid'):
            n = qs.update(last_uid=0)
            self.stdout.write(self.style.WARNING(f'Reset last_uid=0 en {n} sync(s)'))

        # Orden: last_sync_at ASC NULLS FIRST → nunca-corridos primero, después
        # los más viejos. Garantiza fairness ante crashes/timeouts: ningún label
        # queda olvidado siempre, todos rotan eventualmente.
        # Sin --label, además limitamos por --max-labels para chunks predecibles.
        qs = qs.order_by(F('last_sync_at').asc(nulls_first=True), 'buzon__email')
        if not options.get('label'):
            qs = qs[:options['max_labels']]

        if not qs.exists():
            if not options['quiet']:
                self.stdout.write('No hay BuzonGmailLabel activos para sincronizar.')
            return

        # ─── Lock anti-solapamiento ────────────────────────────────────────
        # Si REDIS_URL está, este lock es compartido entre todos los gunicorn
        # workers Y entre comandos manage.py disparados desde el host. Sin
        # Redis (LocMemCache), el lock solo cubre dentro del mismo proceso,
        # pero como `manage.py sincronizar_gmail` corre como su propio proceso
        # cada vez, el lock LocMemCache no sirve — la defensa fuerte queda
        # en la UniqueConstraint de la DB (migración 0022) + IntegrityError
        # más abajo.
        if not options['ignore_lock']:
            if cache.get(SYNC_LOCK_KEY):
                if not options['quiet']:
                    self.stdout.write(self.style.WARNING(
                        'Otro sincronizar_gmail ya está corriendo. Salgo limpio. '
                        '(Pasá --ignore-lock para forzar si sabés que es residual.)'
                    ))
                return
            # set + TTL — si el sync se cuelga, el lock expira solo en 30 min.
            cache.set(SYNC_LOCK_KEY, time.time(), SYNC_LOCK_TTL)

        try:
            total_nuevos_global = 0
            total_dedup_global  = 0
            total_errores_global = 0

            for sync in qs:
                n_nuevos, n_dedup, n_err = self._sync_one(sync, quiet=options['quiet'])
                total_nuevos_global += n_nuevos
                total_dedup_global  += n_dedup
                total_errores_global += n_err

            if not options['quiet'] or total_nuevos_global > 0:
                self.stdout.write(self.style.SUCCESS(
                    f'\nResumen sync · nuevos={total_nuevos_global} · '
                    f'dedup={total_dedup_global} · errores={total_errores_global}'
                ))
        finally:
            # Liberar el lock siempre, incluso si hubo excepciones.
            if not options['ignore_lock']:
                cache.delete(SYNC_LOCK_KEY)

    def _sync_one(self, sync: BuzonGmailLabel, quiet: bool = False) -> tuple[int, int, int]:
        if not quiet:
            self.stdout.write(
                f'\n→ {sync.label_name} → {sync.buzon.email} (last_uid={sync.last_uid})'
            )

        # Cargar mensaje_ids existentes ANTES de iterar para tener dedup en RAM.
        # Se mantiene también después de fetch_nuevos generator — el iterator
        # rinde uno por vez, no carga todos a memoria.
        existing_msgids = set(
            sync.buzon.correos.exclude(mensaje_id='').values_list('mensaje_id', flat=True)
        )

        nuevos = 0
        dedup  = 0
        errores = 0
        max_uid = sync.last_uid
        hubo_algo = False    # True si al menos 1 mensaje pasó por el iterator

        try:
            mensajes_iter = fetch_nuevos(sync.label_name, sync.last_uid)
        except OverquotaError as e:
            # No debería pasar acá (fetch_nuevos es lazy generator) pero
            # por defensa.
            self._registrar_overquota(sync, str(e), quiet)
            return 0, 0, 1
        except ImapError as e:
            sync.error_msg = str(e)[:1000]
            sync.last_sync_at = timezone.now()
            sync.save(update_fields=['error_msg', 'last_sync_at'])
            self.stderr.write(self.style.ERROR(f'  IMAP: {e}'))
            return 0, 0, 1
        except Exception as e:
            sync.error_msg = f'Inesperado: {e}'[:1000]
            sync.last_sync_at = timezone.now()
            sync.save(update_fields=['error_msg', 'last_sync_at'])
            self.stderr.write(self.style.ERROR(f'  ERROR: {e}'))
            return 0, 0, 1

        try:
            for uid, raw in mensajes_iter:
                hubo_algo = True
                try:
                    if uid > max_uid:
                        max_uid = uid

                    msg = email_lib.message_from_bytes(raw)

                    # NUL bytes que Postgres rechaza
                    msg_id = (msg.get('Message-ID', '') or '').replace('\x00', '')[:500]

                    # Dedup en memoria — la constraint DB también lo cubre via
                    # IntegrityError abajo, pero esto evita pegarle al INSERT.
                    if msg_id and msg_id in existing_msgids:
                        dedup += 1
                        continue

                    asunto    = decodificar_header(msg.get('Subject', '')).replace('\x00', '')
                    remitente = decodificar_header(msg.get('From', '')).replace('\x00', '')
                    dest      = decodificar_header(msg.get('To', '')).replace('\x00', '')
                    fecha_str = msg.get('Date', '')

                    fecha = None
                    if fecha_str:
                        try:
                            parsed = email.utils.parsedate_to_datetime(fecha_str)
                            if parsed.tzinfo is None:
                                parsed = timezone.make_aware(parsed)
                            fecha = parsed
                        except Exception:
                            # Fecha mal-formateada en el correo original. Lo guardamos
                            # con fecha=None y seguimos. Trazamos para diagnóstico.
                            logger.warning('Fecha no parseable %r en uid=%s', fecha_str, uid)

                    texto, html = extraer_cuerpos(msg)
                    texto = texto.replace('\x00', '')
                    html  = html.replace('\x00', '')
                    adjuntos_data = extraer_adjuntos(msg)

                    try:
                        with transaction.atomic():
                            correo = Correo.objects.create(
                                buzon=sync.buzon,
                                tipo_carpeta=sync.tipo_carpeta,
                                mensaje_id=msg_id,
                                remitente=remitente[:500],
                                destinatario=dest[:1000],
                                asunto=asunto[:1000],
                                fecha=fecha,
                                cuerpo_texto=texto,
                                cuerpo_html=html,
                                tiene_adjunto=bool(adjuntos_data),
                            )
                            for nombre, mime, payload, content_id in adjuntos_data:
                                adj = Adjunto(
                                    correo=correo,
                                    nombre_original=nombre,
                                    mime_type=mime[:200],
                                    tamano_bytes=len(payload),
                                    content_id=content_id,
                                )
                                adj.archivo.save(nombre, ContentFile(payload), save=False)
                                adj.save()
                    except IntegrityError:
                        # La UniqueConstraint partial (migración 0022) sobre
                        # (buzon, mensaje_id) lo detectó: otro proceso o un sync
                        # paralelo ya insertó este correo.
                        dedup += 1
                        if msg_id:
                            existing_msgids.add(msg_id)
                        continue

                    if msg_id:
                        existing_msgids.add(msg_id)
                    nuevos += 1

                except Exception as e:
                    errores += 1
                    if errores <= 3:
                        self.stderr.write(f'  Error msg uid={uid}: {e}')

        except OverquotaError as e:
            # Gmail nos rate-limiteó en el medio del fetch. Persistimos parcial
            # y marcamos el flag para que próximas corridas salgan limpias 24h.
            self._registrar_overquota(sync, str(e), quiet, nuevos, dedup, max_uid)
            return nuevos, dedup, errores + 1
        except ImapError as e:
            sync.error_msg = str(e)[:1000]
            sync.last_uid = max_uid
            sync.last_sync_at = timezone.now()
            sync.correos_sincronizados += nuevos
            sync.save(update_fields=[
                'last_uid', 'last_sync_at', 'correos_sincronizados', 'error_msg',
            ])
            self.stderr.write(self.style.ERROR(f'  IMAP en medio del fetch: {e}'))
            return nuevos, dedup, errores + 1

        # Persiste el progreso final (todo OK o errores puntuales por mensaje)
        sync.last_uid = max_uid
        sync.last_sync_at = timezone.now()
        sync.correos_sincronizados += nuevos
        sync.error_msg = ''
        sync.save(update_fields=[
            'last_uid', 'last_sync_at', 'correos_sincronizados', 'error_msg',
        ])

        if not quiet:
            if hubo_algo:
                self.stdout.write(self.style.SUCCESS(
                    f'  +{nuevos} nuevos · dedup {dedup} · errores {errores} · last_uid={max_uid}'
                ))
            else:
                self.stdout.write('  (sin novedades)')
        return nuevos, dedup, errores

    def _registrar_overquota(
        self, sync: BuzonGmailLabel, err: str, quiet: bool,
        nuevos: int = 0, dedup: int = 0, max_uid: int = 0,
    ) -> None:
        """
        Marca la pausa global por OVERQUOTA en cache (24h) y persiste el
        estado del sync con el último UID procesado para que cuando Gmail
        desbloquee, retomemos donde quedamos.
        """
        cache.set(OVERQUOTA_PAUSE_KEY, int(time.time()) + OVERQUOTA_PAUSE_SEG,
                  OVERQUOTA_PAUSE_SEG)
        sync.error_msg = f'OVERQUOTA: {err}'[:1000]
        if max_uid > sync.last_uid:
            sync.last_uid = max_uid
            sync.correos_sincronizados += nuevos
        sync.last_sync_at = timezone.now()
        sync.save(update_fields=[
            'last_uid', 'last_sync_at', 'correos_sincronizados', 'error_msg',
        ])
        if not quiet:
            self.stderr.write(self.style.ERROR(
                f'  ⚠️  OVERQUOTA: {err}\n'
                f'  → Sync pausado 24h. Próximas corridas saldrán limpias '
                f'hasta que Gmail desbloquee.'
            ))
