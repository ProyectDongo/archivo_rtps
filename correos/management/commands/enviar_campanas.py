"""
Cron job: envía las campañas cuyo día del mes coincida con HOY.

Idempotencia: el modelo `EnvioCampana` tiene unique_together
(campana, fecha, email). Si el cron corre 2 veces el mismo día, las
inserciones fallan silenciosamente y los emails NO se reenvían.

Para producción agregar al cron del container:
    */30 * * * *  python manage.py enviar_campanas

Flags:
    --dry-run       no envía, solo lista qué mandaría
    --campana ID    solo procesa esa campaña
    --force         ignora el chequeo de día (útil para test/reenvío manual)
    --hora-min HH   solo corre si la hora actual >= hora_envio - margen
                    (default 0: corre siempre que sea el día correcto)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.utils import timezone

from archivo.email_utils import build_brand_logo
from correos.models import CampanaCorreo, EnvioCampana
from correos.templatetags.correos_tags import html_a_texto, render_firma_html, render_firma_texto
from correos.views.campanas import _render_merge

logger = logging.getLogger('correos.enviar_campanas')


class Command(BaseCommand):
    help = 'Envía las campañas de correos automáticos cuyo día del mes coincide con hoy.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='No envía, solo lista.')
        parser.add_argument('--campana', type=int, default=None, help='Solo esa campaña.')
        parser.add_argument('--force', action='store_true', help='Ignora chequeo de día.')
        parser.add_argument('--hora-min', type=int, default=0,
                            help='Solo corre si hora_envio <= now + margen (minutos). 0=siempre.')

    def handle(self, *args, **opts):
        dry         = opts['dry_run']
        force       = opts['force']
        campana_id  = opts['campana']
        margen_min  = opts['hora_min']

        ahora_local = timezone.localtime()
        hoy = ahora_local.date()
        dia = hoy.day

        qs = CampanaCorreo.objects.filter(activa=True).select_related('buzon')
        if campana_id:
            qs = qs.filter(id=campana_id)

        candidatas = []
        for c in qs:
            if not force and dia not in (c.dias_del_mes or []):
                continue
            if margen_min > 0:
                hora_envio_dt = ahora_local.replace(
                    hour=c.hora_envio.hour, minute=c.hora_envio.minute, second=0, microsecond=0,
                )
                if ahora_local < hora_envio_dt - timedelta(minutes=margen_min):
                    continue
            candidatas.append(c)

        if not candidatas:
            self.stdout.write(f'[{ahora_local:%Y-%m-%d %H:%M}] No hay campañas para hoy (día {dia}).')
            return

        self.stdout.write(f'[{ahora_local:%Y-%m-%d %H:%M}] {len(candidatas)} campaña(s) candidatas:')

        total_ok = total_err = total_skip = 0
        for c in candidatas:
            ok, err, skip = self._procesar_campana(c, hoy, dry)
            total_ok += ok
            total_err += err
            total_skip += skip

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Resumen: {total_ok} enviados, {total_err} errores, {total_skip} skip (ya enviados hoy).'
        ))

    def _procesar_campana(self, campana: CampanaCorreo, hoy, dry: bool) -> tuple[int, int, int]:
        self.stdout.write(f'  ➤ "{campana.nombre}" (buzón {campana.buzon.email})')

        # ─── Construir lista de destinatarios única (email → ctx) ────────────
        dest_map: dict[str, dict] = {}

        # Listas asignadas
        for lista in campana.listas.filter(activa=True):
            for ctc in lista.contactos.filter(activo=True):
                key = ctc.email.lower().strip()
                if not key or key in dest_map:
                    continue
                dest_map[key] = {
                    'nombre': ctc.nombre or '',
                    'email':  ctc.email,
                    'extra':  ctc.datos_extra or {},
                }

        # Emails extras sueltos (sin nombre ni extra)
        for email in campana.emails_extra_lista():
            key = email.lower().strip()
            if not key or key in dest_map:
                continue
            dest_map[key] = {'nombre': '', 'email': email, 'extra': {}}

        if not dest_map:
            self.stdout.write(self.style.WARNING('    (sin destinatarios — skip)'))
            return 0, 0, 0

        self.stdout.write(f'    {len(dest_map)} destinatario(s) total.')

        # ─── Brand logo embebido (CID) — una sola vez por campaña ────────────
        brand_ctx, brand_inline = build_brand_logo()

        # ─── Firma del buzón ────────────────────────────────────────────────
        firma_html = render_firma_html(campana.buzon) or ''
        firma_txt  = render_firma_texto(campana.buzon) or ''

        ok = err = skip = 0
        from django.conf import settings
        from email import encoders
        from email.mime.base import MIMEBase

        for email, ctx_dest in dest_map.items():
            # ─── Idempotencia: si ya hay registro para hoy, skip ──────────
            if EnvioCampana.objects.filter(campana=campana, fecha=hoy, email=email).exists():
                skip += 1
                continue

            # ─── Render mail merge ────────────────────────────────────────
            asunto = _render_merge(campana.asunto, ctx_dest)
            cuerpo = _render_merge(campana.cuerpo_html, ctx_dest)
            html = (
                '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
                'line-height:1.5;color:#222">' + cuerpo + '</div>' + firma_html
            )
            texto = html_a_texto(cuerpo)
            if firma_txt:
                texto = (texto + '\n\n' + firma_txt).strip()

            if dry:
                self.stdout.write(f'    [DRY] → {email}  asunto="{asunto[:60]}"')
                ok += 1
                continue

            # ─── Enviar ─────────────────────────────────────────────────
            from_email = campana.buzon.email or settings.DEFAULT_FROM_EMAIL
            firma_nombre = (campana.buzon.firma_nombre or '').strip()
            if firma_nombre:
                from email.utils import formataddr
                from_email = formataddr((firma_nombre, campana.buzon.email))

            try:
                msg = EmailMultiAlternatives(
                    subject=asunto,
                    body=texto,
                    from_email=from_email,
                    to=[email],
                )
                msg.attach_alternative(html, 'text/html')
                if brand_inline:
                    msg.mixed_subtype = 'related'
                    for nombre, contenido, mime, cid in brand_inline:
                        part = MIMEBase(*mime.split('/', 1))
                        part.set_payload(contenido)
                        encoders.encode_base64(part)
                        part.add_header('Content-Disposition', 'inline', filename=nombre)
                        part.add_header('Content-ID', f'<{cid}>')
                        msg.attach(part)
                msg.send()

                # Loguear éxito (idempotencia se garantiza por unique_together)
                try:
                    with transaction.atomic():
                        EnvioCampana.objects.create(
                            campana=campana, fecha=hoy, email=email,
                            nombre=ctx_dest.get('nombre', ''),
                            estado=EnvioCampana.ESTADO_OK,
                        )
                    ok += 1
                except IntegrityError:
                    skip += 1  # race condition: otro cron lo envió justo antes

            except Exception as e:
                msg_err = str(e)[:500]
                logger.error('Campaña %s → %s falló: %s', campana.id, email, msg_err)
                try:
                    with transaction.atomic():
                        EnvioCampana.objects.create(
                            campana=campana, fecha=hoy, email=email,
                            nombre=ctx_dest.get('nombre', ''),
                            estado=EnvioCampana.ESTADO_ERROR, error_msg=msg_err,
                        )
                except IntegrityError:
                    pass
                err += 1

        self.stdout.write(f'    → {ok} OK, {err} errores, {skip} skip.')
        return ok, err, skip
