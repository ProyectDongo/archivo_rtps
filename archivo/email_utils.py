"""
Helper de envío de emails: safe_send().

Wrapper sobre django.core.mail que:
  - Renderiza pares de templates .html + .txt con un contexto.
  - Soporta adjuntos binarios y partes MIME inline (imágenes CID).
  - Devuelve {'ok': True} o {'ok': False, 'error': str} sin propagar excepciones.
  - Loguea todo a nivel INFO/ERROR para auditoría.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from email import encoders
from email.mime.base import MIMEBase

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger('archivo.email_utils')


def safe_send(
    *,
    asunto: str,
    para: str | Sequence[str],
    template: str,
    contexto: dict,
    from_alias: str | None = None,
    reply_to: list[str] | None = None,
    cc: str | Sequence[str] | None = None,
    headers: dict | None = None,
    adjuntos: list[tuple[str, bytes, str]] | None = None,
    inline_images: list[tuple[str, bytes, str, str]] | None = None,
) -> dict:
    """
    Envía un email renderizando <template>.txt (texto plano) y
    <template>.html (alternativa HTML).

    Args:
        asunto:       Asunto del correo.
        para:         Destinatario o lista de destinatarios.
        template:     Ruta base del template sin extensión.
                      Se buscan <template>.txt y <template>.html.
        contexto:     Dict pasado a los templates.
        from_alias:   Dirección "From". Si None usa DEFAULT_FROM_EMAIL.
        reply_to:     Lista de Reply-To. Si None no se setea.
        cc:           CC destinatarios. String o lista.
        headers:      Dict de headers extra (In-Reply-To, References, etc.).
        adjuntos:     Lista de (nombre, bytes, mime_type) — attachments normales.
        inline_images: Lista de (nombre, bytes, mime_type, content_id) — partes inline.

    Returns:
        {'ok': True} si se envió sin excepciones,
        {'ok': False, 'error': str} si falló.
    """
    if isinstance(para, str):
        para = [para]
    else:
        para = list(para)

    if isinstance(cc, str):
        cc = [cc] if cc else []
    else:
        cc = list(cc) if cc else []

    from_email = from_alias or settings.DEFAULT_FROM_EMAIL

    try:
        cuerpo_txt  = render_to_string(template + '.txt',  contexto)
        cuerpo_html = render_to_string(template + '.html', contexto)
    except Exception as e:
        logger.error('safe_send: error renderizando template %s: %s', template, e)
        return {'ok': False, 'error': f'Template error: {e}'}

    try:
        msg = EmailMultiAlternatives(
            subject=asunto,
            body=cuerpo_txt,
            from_email=from_email,
            to=para,
            cc=cc or [],
            reply_to=reply_to or [],
            headers=headers or {},
        )
        msg.attach_alternative(cuerpo_html, 'text/html')

        # Adjuntos binarios normales
        for nombre, contenido, mime in (adjuntos or []):
            msg.attach(nombre, contenido, mime)

        # Imágenes inline (CID) — se envían como partes MIME con Content-ID.
        # Cuando no hay adjuntos regulares, usamos multipart/related (RFC 2387)
        # para que Gmail encuentre las imágenes dentro del mismo contenedor HTML.
        if inline_images and not adjuntos:
            msg.mixed_subtype = 'related'
        for nombre, contenido, mime, cid in (inline_images or []):
            part = MIMEBase(*mime.split('/', 1))
            part.set_payload(contenido)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'inline', filename=nombre)
            part.add_header('Content-ID', f'<{cid}>')
            msg.attach(part)

        msg.send()
        logger.info('safe_send: enviado "%s" → %s', asunto, para)
        return {'ok': True}

    except Exception as e:
        logger.error('safe_send: fallo al enviar "%s" → %s: %s', asunto, para, e)
        return {'ok': False, 'error': str(e)}
