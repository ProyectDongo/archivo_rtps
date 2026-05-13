"""
Importa archivos .mbox a la base de datos, extrayendo también los adjuntos.

Uso:
    python manage.py import_mbox aledezma@pietramonte.cl --archivo=/ruta/Inbox
    python manage.py import_mbox aledezma@pietramonte.cl --carpeta=/ruta/carpeta/

Por seguridad, los adjuntos se guardan en MEDIA_ROOT/adjuntos/<año>/<mes>/...
con nombre único, fuera del directorio del repositorio.
"""
import email
import email.header
import email.utils
import logging
import mailbox
import re
from pathlib import Path

import chardet
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from correos.models import Adjunto, Buzon, Correo

logger = logging.getLogger('correos.import_mbox')


# Tamaño máximo por adjunto (más grande lo saltamos para no inflar disco)
MAX_ADJUNTO_BYTES = 25 * 1024 * 1024   # 25 MB

# Caracteres no permitidos en filenames (cross-platform safe)
_FILENAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def detectar_carpeta(filename: str) -> str:
    """
    Heurística por nombre de archivo .mbox para inferir si es Inbox / Sent / Otros.
    Acepta variantes en español e inglés que produce Gmail Takeout y exports.
    """
    n = filename.lower()
    if 'sent' in n or 'enviado' in n:
        return 'enviados'
    if 'inbox' in n or 'bandeja' in n or 'recibido' in n:
        return 'inbox'
    return 'otros'


def decodificar_header(valor):
    if not valor:
        return ''
    try:
        partes = email.header.decode_header(valor)
        resultado = []
        for parte, charset in partes:
            if isinstance(parte, bytes):
                if charset:
                    try:
                        resultado.append(parte.decode(charset, errors='replace'))
                    except (LookupError, UnicodeDecodeError):
                        resultado.append(parte.decode('utf-8', errors='replace'))
                else:
                    resultado.append(parte.decode('utf-8', errors='replace'))
            else:
                resultado.append(str(parte))
        return ' '.join(resultado)
    except Exception:
        return str(valor)


def _decodear_payload(parte) -> str:
    """Decodifica el payload de una parte respetando charset, con fallbacks.
    Retorna '' si el resultado parece contenido binario (ZIP, PDF, etc.)."""
    payload = parte.get_payload(decode=True)
    if not payload:
        return ''
    # Guard: si los primeros bytes tienen firma de formato binario conocido, ignorar.
    if payload[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'%PDF', b'\x89PNG', b'GIF8', b'\xff\xd8\xff'):
        return ''
    charset = parte.get_content_charset() or 'utf-8'
    try:
        text = payload.decode(charset, errors='replace')
    except (LookupError, UnicodeDecodeError):
        detected = chardet.detect(payload)
        enc = detected.get('encoding') or 'utf-8'
        try:
            text = payload.decode(enc, errors='replace')
        except Exception:
            text = payload.decode('latin-1', errors='replace')
    # Guard: si >25% son caracteres de control no-texto, es binario disfrazado.
    if len(text) > 100:
        non_text = sum(1 for c in text[:500] if ord(c) < 32 and c not in '\t\n\r')
        if non_text / min(len(text), 500) > 0.25:
            return ''
    return text


def extraer_cuerpos(msg) -> tuple[str, str]:
    """
    Devuelve (cuerpo_texto, cuerpo_html). Reglas:
      - Multipart: junta TODAS las partes text/plain en `texto` y todas las
        text/html en `html`. Las que tienen Content-Disposition: attachment
        se ignoran (esas son adjuntos).
      - No-multipart: clasifica por content_type del mensaje completo.
      - Si NO hay text/plain pero SÍ hay HTML, deriva el texto con strip_tags
        (para que la búsqueda en cuerpo_texto siga funcionando con esos
        correos HTML-only).
      - Trunca texto a 50 KB y HTML a 200 KB.
    """
    from django.utils.html import strip_tags

    textos: list[str] = []
    htmls: list[str] = []

    if msg.is_multipart():
        for parte in msg.walk():
            content_type = parte.get_content_type()
            disposition = str(parte.get('Content-Disposition', '')).lower()
            if 'attachment' in disposition:
                continue
            if content_type == 'text/plain':
                t = _decodear_payload(parte)
                if t:
                    textos.append(t)
            elif content_type == 'text/html':
                h = _decodear_payload(parte)
                if h:
                    htmls.append(h)
    else:
        content_type = msg.get_content_type()
        if content_type in ('text/plain', 'text/html'):
            body = _decodear_payload(msg)
            if body:
                if content_type == 'text/html':
                    htmls.append(body)
                else:
                    textos.append(body)

    texto_final = '\n'.join(textos)[:50000]
    html_final = '\n'.join(htmls)[:200000]

    # Si solo hay HTML, derivá texto plano para que la búsqueda lo encuentre.
    if not texto_final and html_final:
        texto_final = strip_tags(html_final)[:50000]

    return texto_final, html_final


# Compat: algunas comandos legacy importan extraer_texto. Dejamos un wrapper
# que devuelve solo el texto (descarta el HTML) para no romper nada.
def extraer_texto(msg) -> str:
    return extraer_cuerpos(msg)[0]


def _nombre_seguro(nombre: str, fallback: str = 'archivo.bin') -> str:
    """Sanea un filename: sin paths, sin chars peligrosos, max 200 chars."""
    if not nombre:
        return fallback
    # Solo el basename
    nombre = Path(nombre).name
    # Reemplaza chars no permitidos
    nombre = _FILENAME_BAD.sub('_', nombre).strip(' .')
    if not nombre:
        return fallback
    return nombre[:200]


def _extraer_content_id(parte) -> str:
    """
    Devuelve el Content-ID de una parte MIME, sin los angle brackets.
    Formato típico del header: '<5db34974-7359-4231-bea1-d6cca25338e2@gmail.com>'
    Devolvemos: '5db34974-7359-4231-bea1-d6cca25338e2@gmail.com'
    Si no hay Content-ID, '' (no es inline / no se puede mapear desde HTML).
    """
    raw = parte.get('Content-ID') or ''
    return str(raw).strip().strip('<>').strip()[:300]


def extraer_adjuntos(msg):
    """
    Devuelve lista de tuplas (nombre, mime, payload, content_id) para todos
    los adjuntos del mensaje. content_id viene sin <>; '' si no es inline.

    Reglas:
      - Una parte es "adjunto" si Content-Disposition tiene 'attachment' o
        si tiene filename (cubre el caso de imágenes inline con disposition
        'inline; filename="..."').
      - También se considera adjunto cualquier parte con Content-ID — incluso
        sin filename — porque pueden ser imágenes inline referenciadas como
        `<img src="cid:xxx">` sin nombre de archivo (Outlook lo hace).
    """
    adjuntos = []
    if not msg.is_multipart():
        return adjuntos

    for parte in msg.walk():
        disposition = str(parte.get('Content-Disposition', '')).lower()
        content_id = _extraer_content_id(parte)
        es_attachment = 'attachment' in disposition
        tiene_filename = 'filename' in disposition
        # Consideramos adjunto si: 1) attachment explícito, 2) tiene filename
        # (inline con archivo), o 3) tiene Content-ID (imagen embebida sin nombre).
        if not (es_attachment or tiene_filename or content_id):
            continue
        try:
            payload = parte.get_payload(decode=True)
            if not payload:
                continue
            if len(payload) > MAX_ADJUNTO_BYTES:
                continue
            nombre = decodificar_header(parte.get_filename() or '')
            if not nombre and content_id:
                # Inline sin nombre: usamos el cid + extensión del mime para
                # tener algo que mostrar/descargar.
                ext = (parte.get_content_subtype() or 'bin').lower()
                nombre = f'inline-{content_id[:40]}.{ext}'
            nombre = _nombre_seguro(nombre)
            mime = parte.get_content_type() or 'application/octet-stream'
            adjuntos.append((nombre, mime, payload, content_id))
        except Exception:
            # Adjunto corrupto / encoding raro / etc. Lo saltamos pero dejamos
            # rastro — sin esto debugear archivos .mbox grandes era a ciegas.
            logger.warning('Adjunto saltado (parte MIME corrupta)', exc_info=True)
            continue
    return adjuntos


class Command(BaseCommand):
    help = 'Importa archivos .mbox a la base de datos (incluyendo adjuntos)'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email del buzón a importar')
        parser.add_argument('--archivo', type=str, help='Ruta al archivo .mbox')
        parser.add_argument('--dir', dest='dir', type=str,
                            help='Directorio con múltiples .mbox (renombrado desde --carpeta para no chocar con --tipo-carpeta).')
        parser.add_argument('--tipo-carpeta', dest='tipo_carpeta',
                            choices=['inbox', 'enviados', 'otros'],
                            help='Forzar el tipo de carpeta (sobreescribe la heurística por nombre de archivo).')
        parser.add_argument('--limpiar', action='store_true',
                            help='Eliminar correos previos del buzón')
        parser.add_argument('--sin-adjuntos', action='store_true',
                            help='Saltarse extracción de adjuntos (más rápido, menos disco)')
        parser.add_argument('--allow-duplicates', dest='allow_duplicates', action='store_true',
                            help='Desactiva el dedup por (buzon, mensaje_id). Por default se '
                                 'skip-ean correos cuyo Message-ID ya existe en este buzón.')

    def handle(self, *args, **options):
        email_buzon = options['email'].lower().strip()
        skip_adj = options['sin_adjuntos']
        tipo_carpeta_forzado = options.get('tipo_carpeta')
        dedup = not options['allow_duplicates']

        buzon, creado = Buzon.objects.get_or_create(email=email_buzon)
        self.stdout.write(f'{"Creado" if creado else "Existente"}: {email_buzon}')

        if options['limpiar']:
            n, _ = buzon.correos.all().delete()
            self.stdout.write(self.style.WARNING(f'  Eliminados {n} correos previos'))

        # ─── Dedup por (buzon, mensaje_id) ──────────────────────────────────
        # Cargamos los mensaje_id ya importados para este buzón en memoria una
        # sola vez al inicio. Luego cada nuevo correo lo chequeamos contra el
        # set en O(1) antes de insertar. Esto evita duplicados cuando se
        # importan archivos que se solapan (ej. INBOX + carpeta-archivo donde
        # el dueño copió correos en vez de moverlos).
        # Mensajes con mensaje_id='' NO dedupean (se insertan siempre).
        existing_msgids: set[str] = set()
        if dedup and not options['limpiar']:
            existing_msgids = set(
                buzon.correos.exclude(mensaje_id='').values_list('mensaje_id', flat=True)
            )
            self.stdout.write(
                f'  Dedup activo · {len(existing_msgids)} mensaje_id ya en BD'
            )

        # Resuelve archivos
        archivos = []
        if options['archivo']:
            archivos.append(Path(options['archivo']))
        elif options['dir']:
            carpeta = Path(options['dir'])
            archivos = list(carpeta.glob('*.mbox')) + list(carpeta.glob('*.mbx'))
        else:
            raise CommandError('Especifica --archivo o --dir')

        total_correos = 0
        total_adjuntos = 0
        total_errores = 0
        total_dedup = 0

        for ruta in archivos:
            tipo_carpeta = tipo_carpeta_forzado or detectar_carpeta(ruta.name)
            self.stdout.write(f'\nProcesando: {ruta.name}  →  carpeta: {tipo_carpeta}')
            try:
                mbox = mailbox.mbox(str(ruta))
                # Iteracion lazy: NO list(mbox) porque para archivos grandes (19+ GB)
                # carga todos los mensajes parseados en RAM y mata el proceso.

                for i, msg in enumerate(mbox, 1):
                    try:
                        # Postgres rechaza NUL bytes en columnas de texto. Algunos
                        # correos viejos los traen en headers (encoding raro). Los
                        # quitamos antes de cualquier insert para no perder el msg.
                        asunto    = decodificar_header(msg.get('Subject', '')).replace('\x00', '')
                        remitente = decodificar_header(msg.get('From', '')).replace('\x00', '')
                        dest      = decodificar_header(msg.get('To', '')).replace('\x00', '')
                        msg_id    = (msg.get('Message-ID', '') or '').replace('\x00', '')
                        fecha_str = msg.get('Date', '')

                        fecha = None
                        if fecha_str:
                            try:
                                parsed = email.utils.parsedate_to_datetime(fecha_str)
                                if parsed.tzinfo is None:
                                    parsed = timezone.make_aware(parsed)
                                fecha = parsed
                            except Exception:
                                logger.debug('Fecha no parseable %r', fecha_str)

                        # Dedup: si ya importamos este mensaje_id en este buzón, skip.
                        # No dedupea cuando msg_id=='' (sin Message-ID).
                        msg_id_short = msg_id[:500]
                        if dedup and msg_id_short and msg_id_short in existing_msgids:
                            total_dedup += 1
                            if i % 100 == 0:
                                self.stdout.write(f'  ... {i} procesados', ending='\r')
                            continue

                        texto, html = extraer_cuerpos(msg)
                        texto = texto.replace('\x00', '')
                        html  = html.replace('\x00', '')
                        adjuntos_data = [] if skip_adj else extraer_adjuntos(msg)

                        # Crear correo
                        correo = Correo.objects.create(
                            buzon=buzon,
                            tipo_carpeta=tipo_carpeta,
                            mensaje_id=msg_id_short,
                            remitente=remitente[:500],
                            destinatario=dest[:1000],
                            asunto=asunto[:1000],
                            fecha=fecha,
                            cuerpo_texto=texto,
                            cuerpo_html=html,
                            tiene_adjunto=bool(adjuntos_data),
                        )
                        total_correos += 1
                        # Sumamos al set para dedupear también dentro del mismo import
                        # (correos repetidos en el mismo .mbox).
                        if dedup and msg_id_short:
                            existing_msgids.add(msg_id_short)

                        # Guardar adjuntos en el filesystem + crear registros
                        for nombre, mime, payload, content_id in adjuntos_data:
                            adj = Adjunto(
                                correo=correo,
                                nombre_original=nombre,
                                mime_type=mime[:200],
                                tamano_bytes=len(payload),
                                content_id=content_id,
                            )
                            # archivo.save() respeta upload_to='adjuntos/%Y/%m/'
                            # y agrega sufijo único si hay colisión
                            adj.archivo.save(nombre, ContentFile(payload), save=False)
                            adj.save()
                            total_adjuntos += 1

                        if i % 100 == 0:
                            self.stdout.write(f'  ... {i} procesados', ending='\r')

                    except Exception as e:
                        total_errores += 1
                        if total_errores <= 5:
                            self.stderr.write(f'  Error msg {i}: {e}')

            except Exception as e:
                self.stderr.write(f'Error abriendo {ruta}: {e}')

        # Actualiza contador
        buzon.total_correos = buzon.correos.count()
        buzon.save(update_fields=['total_correos'])

        self.stdout.write(self.style.SUCCESS(
            f'\nImportación completada:\n'
            f'  Correos nuevos:    {total_correos}\n'
            f'  Adjuntos:          {total_adjuntos}\n'
            f'  Duplicados skip:   {total_dedup}\n'
            f'  Errores:  {total_errores}\n'
            f'  Total en BD: {buzon.total_correos}'
        ))
