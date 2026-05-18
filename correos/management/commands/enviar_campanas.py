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


def _render_merge(template_str: str, ctx: dict) -> str:
    """Render mail merge — duplicado de views/campanas.py para evitar import circular."""
    from django.template import Context, Template, TemplateSyntaxError
    try:
        return Template(template_str).render(Context(ctx))
    except TemplateSyntaxError:
        return template_str

logger = logging.getLogger('correos.enviar_campanas')


class Command(BaseCommand):
    help = 'Envía las campañas de correos automáticos cuyo día del mes coincide con hoy.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='No envía, solo lista.')
        parser.add_argument('--campana', type=int, default=None, help='Solo esa campaña.')
        parser.add_argument('--force', action='store_true', help='Ignora chequeo de día.')
        parser.add_argument('--hora-min', type=int, default=0,
                            help='Solo corre si hora_envio <= now + margen (minutos). 0=siempre.')
        parser.add_argument('--simular-fecha', type=str, default=None,
                            help='Pretende que hoy es esta fecha (YYYY-MM-DD). Útil para '
                                 'validar que día 15 con meses [3,6,9,12] dispara correctamente. '
                                 'Combinar SIEMPRE con --dry-run para no enviar de verdad.')

    def handle(self, *args, **opts):
        from datetime import datetime as _dt
        dry         = opts['dry_run']
        force       = opts['force']
        campana_id  = opts['campana']
        margen_min  = opts['hora_min']
        simular     = opts['simular_fecha']

        ahora_local = timezone.localtime()
        if simular:
            try:
                fecha_sim = _dt.strptime(simular, '%Y-%m-%d').date()
            except ValueError:
                self.stdout.write(self.style.ERROR(
                    f'--simular-fecha "{simular}" inválido. Formato esperado: YYYY-MM-DD.'
                ))
                return
            if not dry:
                self.stdout.write(self.style.WARNING(
                    '⚠ --simular-fecha sin --dry-run podría crear EnvioCampana con fecha '
                    'inventada. Forzando --dry-run.'
                ))
                dry = True
            hoy = fecha_sim
            self.stdout.write(self.style.NOTICE(
                f'[SIMULANDO fecha {hoy} — día {hoy.day}, mes {hoy.month}]'
            ))
        else:
            hoy = ahora_local.date()
        dia = hoy.day
        mes = hoy.month

        qs = CampanaCorreo.objects.filter(activa=True).select_related('buzon')
        if campana_id:
            qs = qs.filter(id=campana_id)

        candidatas = []
        for c in qs:
            if not force:
                if dia not in (c.dias_del_mes or []):
                    continue
                if not c.mes_activo(mes):
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
        ok, err, skip, _detalle = ejecutar_campana(campana, hoy, dry=dry, stdout=self.stdout)
        return ok, err, skip


def ejecutar_campana(campana, hoy, dry: bool = False, stdout=None):
    """
    Lógica de envío reutilizable — usada por el cron y por el endpoint
    "Ejecutar ahora" de la UI.

    Returns: (ok, err, skip, detalle_lista) donde detalle_lista es
    [(email, estado, error_msg), ...] para mostrar en la UI.
    """
    from django.conf import settings
    from django.template.loader import render_to_string
    from email import encoders
    from email.mime.base import MIMEBase
    from email.utils import formataddr

    def log(msg):
        if stdout:
            stdout.write(msg)

    # Construir destinatarios
    dest_map: dict[str, dict] = {}
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
    for email in campana.emails_extra_lista():
        key = email.lower().strip()
        if key and key not in dest_map:
            dest_map[key] = {'nombre': '', 'email': email, 'extra': {}}

    if not dest_map:
        log('    (sin destinatarios — skip)')
        return 0, 0, 0, []

    log(f'    {len(dest_map)} destinatario(s).')

    brand_ctx, brand_inline = build_brand_logo()
    firma_txt = render_firma_texto(campana.buzon) or ''

    ok = err = skip = 0
    detalle = []

    for email, ctx_dest in dest_map.items():
        if EnvioCampana.objects.filter(campana=campana, fecha=hoy, email=email).exists():
            skip += 1
            detalle.append((email, 'skip', 'ya enviado hoy'))
            continue

        asunto = _render_merge(campana.asunto, ctx_dest)
        cuerpo = _render_merge(campana.cuerpo_html, ctx_dest)

        # Usar el mismo wrapper que los emails de compose: header con logo,
        # barras de acento, firma del buzón y footer "ISO 9001".
        html = render_to_string('correos/email/compose.html', {
            'asunto': asunto,
            'cuerpo_usuario': cuerpo,
            'buzon': campana.buzon,
            **brand_ctx,
        })
        texto = html_a_texto(cuerpo)
        if firma_txt:
            texto = (texto + '\n\n' + firma_txt).strip()

        if dry:
            log(f'    [DRY] → {email}  "{asunto[:50]}"')
            detalle.append((email, 'dry', f'simulado: {asunto[:60]}'))
            ok += 1
            continue

        from_email = campana.buzon.email or settings.DEFAULT_FROM_EMAIL
        firma_nombre = (campana.buzon.firma_nombre or '').strip()
        if firma_nombre:
            from_email = formataddr((firma_nombre, campana.buzon.email))

        try:
            msg = EmailMultiAlternatives(
                subject=asunto, body=texto, from_email=from_email, to=[email],
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

            try:
                with transaction.atomic():
                    EnvioCampana.objects.create(
                        campana=campana, fecha=hoy, email=email,
                        nombre=ctx_dest.get('nombre', ''),
                        estado=EnvioCampana.ESTADO_OK,
                    )
                ok += 1
                detalle.append((email, 'ok', ''))
            except IntegrityError:
                skip += 1
                detalle.append((email, 'skip', 'race condition'))

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
            detalle.append((email, 'error', msg_err))

    log(f'    → {ok} OK, {err} errores, {skip} skip.')
    return ok, err, skip, detalle
