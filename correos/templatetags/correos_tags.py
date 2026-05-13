"""
Template tags personalizados.
"""
import hashlib
import re

from django import template
from django.conf import settings
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


# Paleta para avatares — colores derivados del logo Pietramonte (rojo + grafitos).
_AVATAR_COLORS = [
    ('#C80C0F', '#ffffff'),  # rojo
    ('#394348', '#ffffff'),  # grafito medio
    ('#1a1f22', '#ffffff'),  # grafito oscuro
    ('#9a0a0c', '#ffffff'),  # rojo oscuro
    ('#2c5364', '#ffffff'),  # azul acero
    ('#5d4037', '#ffffff'),  # marrón
    ('#37474f', '#ffffff'),  # gris azulado
    ('#6d4c41', '#ffffff'),  # marrón claro
]


_DIAS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
_MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
          'jul', 'ago', 'sep', 'oct', 'nov', 'dic']


@register.filter
def fecha_amigable(dt):
    """
    Devuelve la fecha legible. SIEMPRE incluye día/mes (y año cuando aplica).
    No oculta la fecha real para correos viejos: el usuario debe poder ver
    cuándo es un correo SIN tener que abrirlo.

    Formato:
      - Hoy 14:32
      - Ayer 09:15
      - Lun 12 may · 14:32      (esta semana, dentro del año)
      - 12 may · 14:32          (este año, fuera de la última semana)
      - 12 may 2024 · 14:32     (años anteriores, fecha completa)
    """
    if not dt:
        return '—'

    ahora = timezone.localtime(timezone.now())
    dt_local = timezone.localtime(dt)
    delta = ahora.date() - dt_local.date()

    hora = f'{dt_local:%H:%M}'

    if delta.days == 0:
        return f'Hoy {hora}'
    if delta.days == 1:
        return f'Ayer {hora}'
    if 0 < delta.days < 7 and dt_local.year == ahora.year:
        return f'{_DIAS[dt_local.weekday()]} {dt_local.day} {_MESES[dt_local.month - 1]} · {hora}'
    if dt_local.year == ahora.year:
        return f'{dt_local.day} {_MESES[dt_local.month - 1]} · {hora}'
    return f'{dt_local.day} {_MESES[dt_local.month - 1]} {dt_local.year} · {hora}'


@register.filter
def fecha_iso(dt):
    """Fecha completa para tooltips: '2024-05-12 14:32:18 (-04)'."""
    if not dt:
        return ''
    dt_local = timezone.localtime(dt)
    return dt_local.strftime('%Y-%m-%d %H:%M:%S (%z)')


_RE_EMAIL_BRACKET = re.compile(r'<[^>]+>')


def _ini_email(direccion: str) -> str:
    """Iniciales de un email "bare": 'a@b.cl' → 'AB', 'oficina@rtsp.cl' → 'OR'."""
    local, _, domain = direccion.partition('@')
    return ((local[:1] or '?') + (domain[:1] or '?')).upper()


@register.filter
def avatar_iniciales(texto):
    """
    Devuelve hasta 2 letras iniciales del remitente.

    Casos:
      'Ana Ledezma'                       → 'AL'
      'Rodrigo Del saz <a@b.cl>'          → 'RS'   (sin contar el email)
      '<solo@email.cl>'                   → 'SE'   (local + domain del email)
      'oficina@rtsp.cl'                   → 'OR'
      'soporte'                           → 'SO'
      ''                                  → '?'

    Antes el bug: 'Rodrigo Del saz <a@b.cl>' producía 'R<' porque '<a@b.cl>'
    se contaba como una palabra y su primer char era '<'.
    """
    if not texto:
        return '?'
    # Quitar "<email>" si el texto trae 'Nombre <email>'.
    limpio = _RE_EMAIL_BRACKET.sub('', str(texto)).strip().strip('"\' ')
    palabras = [p for p in limpio.split() if p]

    # Caso: el texto era solo '<email>' (sin nombre). Volver al original sin <>.
    if not palabras:
        bare = str(texto).strip().strip('<>"\' ')
        if '@' in bare:
            return _ini_email(bare)
        return bare[:2].upper() if bare else '?'

    # Una sola palabra: si es un email, partir por @; sino tomar 2 primeros chars.
    if len(palabras) == 1:
        p = palabras[0]
        if '@' in p:
            return _ini_email(p)
        return p[:2].upper()

    return (palabras[0][0] + palabras[-1][0]).upper()


@register.filter
def avatar_color(texto):
    """Color determinístico para un avatar dado un string (email/nombre)."""
    if not texto:
        return _AVATAR_COLORS[0][0]
    h = int(hashlib.md5(str(texto).encode()).hexdigest()[:8], 16)
    return _AVATAR_COLORS[h % len(_AVATAR_COLORS)][0]


# Mapeo MIME → categoría visual usada en la galería de adjuntos.
# Image, pdf, sheet, doc, slides, zip, audio, video, code, text, otro.
_TIPO_BY_PREFIX = {
    'image/': 'imagen',
    'audio/': 'audio',
    'video/': 'video',
    'text/':  'texto',
}
_TIPO_BY_EXACT = {
    'application/pdf':                                                         'pdf',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':       'sheet',
    'application/vnd.ms-excel':                                                'sheet',
    'application/vnd.oasis.opendocument.spreadsheet':                          'sheet',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'doc',
    'application/msword':                                                      'doc',
    'application/vnd.oasis.opendocument.text':                                 'doc',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'slides',
    'application/vnd.ms-powerpoint':                                           'slides',
    'application/zip':                                                         'zip',
    'application/x-zip-compressed':                                            'zip',
    'application/x-rar-compressed':                                            'zip',
    'application/x-7z-compressed':                                             'zip',
    'application/json':                                                        'codigo',
    'application/javascript':                                                  'codigo',
    'application/xml':                                                         'codigo',
}


@register.filter
def tipo_archivo(adjunto):
    """Devuelve la categoría visual ('imagen' / 'pdf' / 'doc' / ...) de un Adjunto."""
    mime = (getattr(adjunto, 'mime_type', '') or '').lower().strip()
    if mime in _TIPO_BY_EXACT:
        return _TIPO_BY_EXACT[mime]
    for prefijo, t in _TIPO_BY_PREFIX.items():
        if mime.startswith(prefijo):
            return t
    return 'otro'


@register.filter
def es_imagen(adjunto):
    """True si el adjunto es una imagen renderizable inline."""
    mime = (getattr(adjunto, 'mime_type', '') or '').lower()
    return mime.startswith('image/')


@register.filter
def dict_get(d, key):
    """Subscript con clave variable: {{ mi_dict|dict_get:obj.id }}."""
    if not d:
        return None
    try:
        return d.get(key)
    except AttributeError:
        return None


# ─── Sanitización de HTML de email ─────────────────────────────────────────
# bleach + tinycss2 (extras [css]) — limpia tags peligrosos, eventos JS,
# javascript: URLs, y propiedades CSS arbitrarias. Tres cleaners cacheados:
#
#   - INBOUND_STRICT (legacy filter `sanitizar_email_html`): strip todas las
#     <img>. Se mantiene por compatibilidad pero ya no se usa en los templates.
#
#   - INBOUND_SAFE_IMGS (`render_correo_html` simple_tag): permite <img> SOLO
#     con src relativa (nuestras URLs internas para cid:) o data:image. Bloquea
#     URLs externas (anti tracking-pixel). Asume que `cid:` ya fue pre-resuelto
#     por _resolver_cid_en_html() ANTES de invocar el cleaner.
#
#   - OUTBOUND (emails que enviamos): permite <img>, cid:, data: porque van
#     adjuntos del lado nuestro y el destinatario los embebe.
_EMAIL_CLEANER_INBOUND = None
_EMAIL_CLEANER_INBOUND_SAFE_IMGS = None
_EMAIL_CLEANER_OUTBOUND = None


_CSS_PROPS = [
    'color', 'background-color',
    'font', 'font-family', 'font-size', 'font-weight', 'font-style',
    'font-variant', 'line-height', 'letter-spacing', 'text-align',
    'text-decoration', 'text-transform', 'text-indent', 'white-space',
    'vertical-align',
    'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'padding', 'padding-top', 'padding-bottom', 'padding-left', 'padding-right',
    'border', 'border-top', 'border-bottom', 'border-left', 'border-right',
    'border-color', 'border-style', 'border-width', 'border-radius',
    'border-collapse', 'border-spacing',
    'width', 'height', 'min-width', 'min-height', 'max-width', 'max-height',
    'display', 'list-style', 'list-style-type', 'list-style-position',
    'overflow', 'word-wrap', 'word-break',
]


def _img_attr_filter_safe(tag, name, value):
    """
    Filtro de atributos para <img> en modo INBOUND_SAFE_IMGS.

    src permitido si:
      - empieza con '/'  (URL interna nuestra, ej. /intranet/correo/X/cid/Y)
      - empieza con 'data:image/'  (imagen base64 inline, signatures)
      - empieza con 'http://' o 'https://' Y settings.EMAIL_ALLOW_EXTERNAL_IMAGES=True
        (default True — fix 2026-05-11 para que logos/marca se vean tipo Gmail).

    Imágenes externas pueden ser tracking pixels. Mitigaciones:
      - `referrerpolicy="no-referrer"` (oculta dominio/path del portal al sender)
      - `loading="lazy"` (no descarga hasta que el user scrollea al correo)
      - Ambos atributos se inyectan automáticamente en `render_correo_html`
        después del cleaner (ver _inject_img_safety_attrs).

    Para volver al comportamiento estricto (bloquear todas las img externas),
    setear EMAIL_ALLOW_EXTERNAL_IMAGES=False en .env.

    javascript:, file:, ftp:, etc. siempre se bloquean.
    """
    if name in {'alt', 'width', 'height', 'border', 'title', 'style', 'class',
                'referrerpolicy', 'loading'}:
        return True
    if name == 'src':
        v = (value or '').strip()
        if v.startswith('/'):
            return True
        vl = v.lower()
        if vl.startswith('data:image/'):
            return True
        if vl.startswith('http://') or vl.startswith('https://'):
            from django.conf import settings
            return bool(getattr(settings, 'EMAIL_ALLOW_EXTERNAL_IMAGES', True))
        return False
    return False


def _make_email_cleaner(modo: str):
    """
    modo ∈ {'inbound_strict', 'inbound_safe_imgs', 'outbound'}.
    """
    import bleach
    from bleach.css_sanitizer import CSSSanitizer

    tags = {
        'p', 'br', 'hr', 'div', 'span', 'blockquote', 'pre', 'code',
        'strong', 'b', 'em', 'i', 'u', 's', 'sup', 'sub', 'font',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li', 'dl', 'dt', 'dd',
        'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th',
        'caption', 'colgroup', 'col',
        'a',
    }
    attrs = {
        '*':       ['class', 'style', 'align', 'valign', 'dir', 'title', 'lang'],
        'a':       ['href', 'name', 'target', 'rel', 'title'],
        'table':   ['border', 'cellpadding', 'cellspacing', 'width', 'height', 'summary'],
        'td':      ['colspan', 'rowspan', 'width', 'height', 'align', 'valign', 'nowrap'],
        'th':      ['colspan', 'rowspan', 'width', 'height', 'align', 'valign', 'scope'],
        'tr':      ['align', 'valign'],
        'col':     ['span', 'width'],
        'colgroup':['span', 'width'],
        'font':    ['color', 'face', 'size'],
    }
    protocols = ['http', 'https', 'mailto', 'tel']

    if modo == 'outbound':
        tags.add('img')
        attrs['img'] = ['src', 'alt', 'width', 'height', 'border', 'title', 'style']
        # cid: para imágenes inline del propio email; data: para base64.
        protocols.extend(['data', 'cid'])
    elif modo == 'inbound_safe_imgs':
        tags.add('img')
        # Filtro callable: bleach llama img_attr_filter(tag, name, value) por
        # cada atributo. La validación de src vive ahí (ver _img_attr_filter_safe).
        attrs['img'] = _img_attr_filter_safe
        # data: necesario para que el sanitizer no bloquee data:image/... por protocol.
        protocols.append('data')
    elif modo != 'inbound_strict':
        raise ValueError(f'modo desconocido: {modo}')

    css_sanitizer = CSSSanitizer(allowed_css_properties=_CSS_PROPS)
    return bleach.Cleaner(
        tags=tags,
        attributes=attrs,
        protocols=protocols,
        css_sanitizer=css_sanitizer,
        strip=True,
        strip_comments=True,
    )


def _email_cleaner_inbound():
    global _EMAIL_CLEANER_INBOUND
    if _EMAIL_CLEANER_INBOUND is None:
        _EMAIL_CLEANER_INBOUND = _make_email_cleaner('inbound_strict')
    return _EMAIL_CLEANER_INBOUND


def _email_cleaner_inbound_safe_imgs():
    global _EMAIL_CLEANER_INBOUND_SAFE_IMGS
    if _EMAIL_CLEANER_INBOUND_SAFE_IMGS is None:
        _EMAIL_CLEANER_INBOUND_SAFE_IMGS = _make_email_cleaner('inbound_safe_imgs')
    return _EMAIL_CLEANER_INBOUND_SAFE_IMGS


def _email_cleaner_outbound():
    global _EMAIL_CLEANER_OUTBOUND
    if _EMAIL_CLEANER_OUTBOUND is None:
        _EMAIL_CLEANER_OUTBOUND = _make_email_cleaner('outbound')
    return _EMAIL_CLEANER_OUTBOUND


# ─── Resolución de cid: a URLs internas ────────────────────────────────────
# `cid:5db34974-...` es la sintaxis MIME para referenciar un adjunto inline
# desde el HTML del mismo correo (ej: `<img src="cid:xxx">` en una signature
# o screenshot embebido). Sin resolver, queda como texto literal "[cid:xxx]"
# en el render → feo y roto.
#
# Captura tanto el formato dentro de una URL `cid:xxx` (en src/href) como
# la forma "marcada como link plain text" `[cid:xxx]` que se cuela cuando
# el cliente convirtió HTML a texto plano y la app lo renderea pre-formato.
_RE_CID_URL = re.compile(r'cid:([^"\'\s>)\]]+)', re.IGNORECASE)
_RE_CID_BRACKETED = re.compile(r'\[\s*cid\s*:\s*([^\]\s]+)\s*\]', re.IGNORECASE)


def _resolver_cid_en_html(html: str, correo) -> str:
    """
    Reemplaza `cid:xxx` en HTML por URLs internas autenticadas para los
    adjuntos del correo dado. Las refs no resueltas quedan tal cual y bleach
    las strippa (cid: no está en el protocols whitelist del cleaner safe-imgs).

    Una sola query a Adjunto: devuelve un dict {content_id: url} y reemplaza
    en bloque.
    """
    if not html or 'cid:' not in html.lower():
        return html

    from django.urls import reverse
    cids = list(correo.adjuntos.exclude(content_id='')
                       .values_list('content_id', flat=True))
    if not cids:
        return html

    cid_to_url = {
        cid: reverse('adjunto_por_cid',
                     kwargs={'correo_id': correo.id, 'content_id': cid})
        for cid in cids
    }

    def repl(m):
        cid = m.group(1).strip().rstrip('>"\')')
        return cid_to_url.get(cid, m.group(0))

    return _RE_CID_URL.sub(repl, html)


def _strip_cid_brackets_en_texto(texto: str) -> str:
    """
    Elimina `[cid:xxx]` del cuerpo en TEXTO PLANO. Cuando el correo tiene
    cuerpo HTML (con su <img> que pre-resolvemos), esta forma de bracket
    aparece como ruido en el fallback de texto plano.

    Solo strippa el bracket — el resto del texto queda intacto.
    """
    if not texto or '[cid:' not in texto.lower():
        return texto
    return _RE_CID_BRACKETED.sub('', texto).rstrip()


# ─── Pre-strip de bloques que bleach deja como texto ──────────────────────
# Bleach con strip=True remueve la tag pero NO el contenido. Para <style>,
# <script>, <head>, etc. esto causa que el CSS/JS aparezca como texto plano
# en el portal. Los limpiamos antes de pasar a bleach.
_RE_BLOQUES_INDESEADOS = re.compile(
    r'<\s*(style|script|head|title|template|noscript|xml|o:[A-Za-z0-9_-]+)\b[^>]*>.*?'
    r'<\s*/\s*\1\s*>',
    re.DOTALL | re.IGNORECASE,
)
_RE_BLOQUES_AUTOCLOSE = re.compile(r'<!\[CDATA\[.*?\]\]>', re.DOTALL)
_RE_HTML_COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)


def _pre_strip_html_para_bleach(html: str) -> str:
    """Quita <style>, <script>, <head>, comentarios y CDATA antes del cleaner."""
    if not html:
        return ''
    html = _RE_BLOQUES_INDESEADOS.sub('', html)
    html = _RE_BLOQUES_AUTOCLOSE.sub('', html)
    html = _RE_HTML_COMMENT.sub('', html)
    return html


@register.filter(is_safe=True)
def sanitizar_email_html(html: str) -> str:
    """
    Sanitiza HTML para mostrar en el PORTAL. Strip <img> (anti tracking-pixels
    y cid: rotos). Bloquea <script>, <iframe>, eventos on*, javascript: URLs,
    background-image: url(...) externos, etc.

    Uso: {{ correo.cuerpo_html|sanitizar_email_html|safe }}
    """
    if not html:
        return ''
    try:
        return _email_cleaner_inbound().clean(_pre_strip_html_para_bleach(html))
    except Exception:
        from django.utils.html import strip_tags
        return strip_tags(html)


@register.simple_tag
def render_correo_body(correo):
    """
    Renderiza el cuerpo del correo eligiendo automáticamente HTML o texto.

    Casos:
      1. cuerpo_html no vacío → render HTML (cid resolution + sanitize + img safety).
      2. cuerpo_html vacío PERO cuerpo_texto parece HTML (empieza con
         <!DOCTYPE, <html, <head, <body, <table, etc.) → tratar el "texto"
         como HTML y renderizarlo. Cubre el bug del importer/sync que en
         algunos correos puso el HTML literal en cuerpo_texto.
      3. cuerpo_texto es texto plano normal → render con render_texto_plano.
      4. Ambos vacíos → string vacío (el template muestra "no tiene cuerpo").

    Uso en template:
        {% render_correo_body correo %}
    """
    from django.utils.safestring import mark_safe

    if correo.cuerpo_html:
        return render_correo_html(correo)

    texto = (correo.cuerpo_texto or '').strip()
    if not texto:
        return ''

    primeros_chars = texto[:500].lower().lstrip()
    es_html_disfrazado = (
        primeros_chars.startswith('<!doctype html')
        or primeros_chars.startswith('<html')
        or primeros_chars.startswith('<?xml')
        or primeros_chars.startswith('<head')
        or primeros_chars.startswith('<body')
        or (primeros_chars.startswith('<table') and '</table>' in texto.lower())
    )

    if es_html_disfrazado:
        try:
            html = _pre_strip_html_para_bleach(texto)
            cleaned = _email_cleaner_inbound_safe_imgs().clean(html)
            cleaned = _inject_img_safety_attrs(cleaned)
            return mark_safe(cleaned)
        except Exception:
            from django.utils.html import strip_tags
            return mark_safe(strip_tags(texto))

    return render_texto_plano(texto)


@register.simple_tag
def render_correo_html(correo):
    """
    Renderiza el HTML de un correo con cid: resueltos a URLs internas y
    sanitización con `<img>` permitido con src interna, data:image, o
    http(s) externa (si EMAIL_ALLOW_EXTERNAL_IMAGES=True, default).

    Uso en template:
        {% render_correo_html correo %}

    Garantías de seguridad:
      1. Pre-pass: `cid:xxx` se mapea a /intranet/correo/<id>/cid/<xxx>
         (URL autenticada que valida acceso al buzón antes de servir).
      2. Bleach con cleaner inbound_safe_imgs: strippa <script>, eventos on*,
         javascript: URLs, CSS arbitrario, etc.
      3. Tags <img> con src no permitido se eliminan por completo.
      4. Postprocess: inyecta `referrerpolicy="no-referrer"` + `loading="lazy"`
         en cada <img> sobreviviente. Mitiga tracking pixels:
         - referrerpolicy oculta dominio/path del portal al server del sender
         - loading=lazy retrasa la descarga hasta que el user scrollee al correo

    Devuelve `mark_safe(html_limpio)` — listo para emitir directo en plantilla.
    """
    from django.utils.safestring import mark_safe

    html = correo.cuerpo_html or ''
    if not html:
        return ''
    try:
        html = _resolver_cid_en_html(html, correo)
        html = _pre_strip_html_para_bleach(html)
        cleaned = _email_cleaner_inbound_safe_imgs().clean(html)
        cleaned = _inject_img_safety_attrs(cleaned)
        return mark_safe(cleaned)
    except Exception:
        from django.utils.html import strip_tags
        return mark_safe(strip_tags(html))


# Regex para detectar <img …> sin referrerpolicy o sin loading y agregarlos.
# Captura: 1=tag completo hasta el espacio antes de cerrar, sin "/>" o ">"
_RE_IMG_TAG = re.compile(r'<img\b([^>]*?)\s*(/?)>', re.IGNORECASE)


def _inject_img_safety_attrs(html: str) -> str:
    """
    Inyecta `referrerpolicy="no-referrer"` y `loading="lazy"` en cada <img>
    que no los tenga ya. Mitiga tracking pixels permitidos por el cleaner
    sin tener que correr otra pasada de bleach.
    """
    if '<img' not in html.lower():
        return html

    def repl(m):
        attrs = m.group(1) or ''
        attrs_lower = attrs.lower()
        extras = []
        if 'referrerpolicy' not in attrs_lower:
            extras.append('referrerpolicy="no-referrer"')
        if 'loading' not in attrs_lower:
            extras.append('loading="lazy"')
        if not extras:
            return m.group(0)
        # Conserva el self-close `/` si existía (XHTML).
        closing = m.group(2) or ''
        sep = ' ' if attrs.strip() else ''
        return f'<img{attrs}{sep}{" ".join(extras)}{closing}>' if closing \
               else f'<img{attrs} {" ".join(extras)}>'

    return _RE_IMG_TAG.sub(repl, html)


@register.filter(is_safe=True)
def limpiar_cid_brackets(texto):
    """
    Filter para usar en el render del cuerpo en TEXTO PLANO. Quita los
    `[cid:xxx]` literales que quedan cuando el correo trae imágenes inline
    pero solo estamos mostrando la versión texto.

    Uso:
        {{ correo.cuerpo_texto|limpiar_cid_brackets }}
    """
    return _strip_cid_brackets_en_texto(texto or '')


_RE_IMAGE_PLACEHOLDER = re.compile(r'\[image:[^\]]*\]', re.IGNORECASE)
_RE_BOLD_MARKDOWN = re.compile(r'\*([^*\n]{1,200})\*')
_RE_LINKIFY = re.compile(r'(https?://[^\s<>\'"]{4,})')
_RE_ANGLE_PHONE = re.compile(r'<(\+[\d\s\-().]{4,})>')


@register.filter
def is_html_content(text):
    t = (text or '').strip()
    return bool(t) and t[0] == '<'


@register.filter(is_safe=True)
def render_texto_plano(texto):
    """
    Renderiza el cuerpo en texto plano con mejoras visuales similares a Gmail:
    - Elimina [image: ...] y [cid:...] (placeholders de imágenes inline)
    - Estila líneas citadas (> ...) como blockquote con borde izquierdo
    - Convierte *bold* en <strong>
    - Linkifica URLs
    - Limpia <+569...> angle brackets de teléfonos
    """
    if not texto:
        return ''

    texto = _strip_cid_brackets_en_texto(texto)
    texto = _RE_IMAGE_PLACEHOLDER.sub('', texto)

    lines = texto.splitlines()
    parts = []
    in_quote = False

    for line in lines:
        m = re.match(r'^(>+)\s?(.*)', line)
        if m:
            content = escape(m.group(2))
            if not in_quote:
                parts.append('<blockquote class="pt-quote">')
                in_quote = True
            parts.append(content + '\n')
        else:
            if in_quote:
                parts.append('</blockquote>')
                in_quote = False
            parts.append(escape(line) + '\n')

    if in_quote:
        parts.append('</blockquote>')

    html = ''.join(parts)

    html = _RE_ANGLE_PHONE.sub(r'\1', html)
    html = _RE_BOLD_MARKDOWN.sub(r'<strong>\1</strong>', html)
    html = _RE_LINKIFY.sub(
        r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
        html,
    )

    return mark_safe(html)


@register.filter(is_safe=True)
def sanitizar_email_html_outbound(html: str) -> str:
    """
    Sanitiza HTML para EMAILS QUE ENVIAMOS (forwards, replies). Permisivo
    con <img>, cid: y data: para que el destinatario vea el formato original
    como en Gmail. Sigue bloqueando scripts, iframes, javascript: URLs, etc.

    Strippa texto literal `[cid:xxx]` que algunos clientes meten cuando
    convierten HTML con imágenes inline a texto plano. Sin esto, el destinatario
    veía "[cid:5db3...]" en el medio del cuerpo (ver screenshots de la sesión
    de UX 2026-05-08).

    Uso: {{ correo.cuerpo_html|sanitizar_email_html_outbound|safe }}
    """
    if not html:
        return ''
    try:
        clean = _email_cleaner_outbound().clean(_pre_strip_html_para_bleach(html))
    except Exception:
        from django.utils.html import strip_tags
        return strip_tags(html)
    return _strip_cid_brackets_en_texto(clean)


@register.simple_tag(takes_context=True)
def url_sin_filtros(context, *quitar):
    """
    Devuelve la URL del inbox con la querystring actual menos las keys listadas.
    Siempre quita `page` también (cambiar un filtro debe llevar a página 1).

    Devuelve una URL absoluta a {% url 'inbox' %} — relativos rompen cuando el
    JS hizo pushState a /intranet/correo/N/ y un click en un chip resolvería
    contra esa URL.

    Uso en template:
        <a href="{% url_sin_filtros 'q' %}">Quitar búsqueda</a>
        <a href="{% url_sin_filtros 'desde' 'hasta' %}">Quitar rango fechas</a>
    """
    from django.urls import reverse
    base = reverse('inbox')
    request = context.get('request')
    if request is None:
        return base
    qs = request.GET.copy()
    for key in quitar:
        qs.pop(key, None)
    qs.pop('page', None)
    encoded = qs.urlencode()
    return f'{base}?{encoded}' if encoded else base


# ─── Render de firma de buzón (auto-append en correos salientes) ──────────
def _icon_circle(unicode_char: str, accent: str) -> str:
    """
    Genera un span con el caracter Unicode dentro de un círculo del color de
    acento. Para que se vea consistente en Gmail/Outlook/Apple Mail usamos
    line-height = height (truco para centrar verticalmente) y display:inline-block.
    """
    return (
        f'<span style="display:inline-block;width:20px;height:20px;'
        f'background:{accent};color:#ffffff;border-radius:50%;'
        f'text-align:center;font-size:11px;line-height:20px;'
        f'margin-right:10px;vertical-align:middle;font-family:Arial,sans-serif">'
        f'{unicode_char}</span>'
    )


def render_firma_html(buzon) -> str:
    """
    Devuelve el HTML de la firma de un buzón con layout MIME-safe (tablas +
    estilos inline) que se ve consistente en Gmail, Outlook, Apple Mail.
    Iconos en círculos del color de acento del deployment (BRAND_PRIMARY_COLOR).
    Si el buzón no tiene firma activa o no tiene datos, devuelve ''.
    """
    if not buzon or not getattr(buzon, 'firma_activa', True):
        return ''

    nombre   = (buzon.firma_nombre or '').strip()
    cargo    = (buzon.firma_cargo or '').strip()
    telefono = (buzon.firma_telefono or '').strip()
    email_v  = (buzon.firma_email_visible or buzon.email or '').strip()
    web      = (getattr(buzon, 'firma_web', '') or '').strip()
    logo_url = getattr(settings, 'FIRMA_LOGO_URL', '') or ''
    accent   = getattr(settings, 'BRAND_PRIMARY_COLOR', '#C80C0F') or '#C80C0F'

    if not (nombre or cargo or telefono or email_v or web or logo_url):
        return ''

    # ─── Datos (columna derecha) ──────────────────────────────────────────
    bloques = []
    if nombre:
        bloques.append(
            f'<div style="font-size:16px;font-weight:700;color:#1a1f22;'
            f'line-height:1.25;letter-spacing:-0.2px">{escape(nombre)}</div>'
        )
    if cargo:
        bloques.append(
            f'<div style="font-size:11px;font-weight:600;letter-spacing:1.5px;'
            f'text-transform:uppercase;color:#6b7280;margin-top:3px">'
            f'{escape(cargo)}</div>'
        )

    # Línea fina del color de acento debajo del nombre/cargo
    bloques.append(
        f'<div style="width:36px;height:2px;background:{accent};'
        f'margin:10px 0 12px"></div>'
    )

    # Filas de contacto
    contact_rows = []
    if telefono:
        contact_rows.append(
            f'<tr><td style="padding:3px 0;font-size:13px;color:#394348;'
            f'line-height:1.4;font-family:-apple-system,BlinkMacSystemFont,'
            f"'Segoe UI',Helvetica,Arial,sans-serif\">"
            f'{_icon_circle("&#9742;", accent)}{escape(telefono)}</td></tr>'
        )
    if email_v:
        contact_rows.append(
            f'<tr><td style="padding:3px 0;font-size:13px;color:#394348;'
            f'line-height:1.4;font-family:-apple-system,BlinkMacSystemFont,'
            f"'Segoe UI',Helvetica,Arial,sans-serif\">"
            f'{_icon_circle("&#9993;", accent)}'
            f'<a href="mailto:{escape(email_v)}" style="color:#394348;'
            f'text-decoration:none">{escape(email_v)}</a></td></tr>'
        )
    if web:
        # Normalizar: si no tiene esquema, agregamos https:// al href.
        href_web = web if web.lower().startswith(('http://', 'https://')) else f'https://{web}'
        contact_rows.append(
            f'<tr><td style="padding:3px 0;font-size:13px;color:#394348;'
            f'line-height:1.4;font-family:-apple-system,BlinkMacSystemFont,'
            f"'Segoe UI',Helvetica,Arial,sans-serif\">"
            f'{_icon_circle("&#9783;", accent)}'
            f'<a href="{escape(href_web)}" style="color:#394348;'
            f'text-decoration:none">{escape(web)}</a></td></tr>'
        )
    if contact_rows:
        bloques.append(
            '<table cellpadding="0" cellspacing="0" border="0" role="presentation" '
            'style="border-collapse:collapse">' + ''.join(contact_rows) + '</table>'
        )

    columna_derecha = (
        f'<td valign="top" style="vertical-align:top;'
        f'padding-left:18px;border-left:3px solid {accent}">'
        + ''.join(bloques)
        + '</td>'
    )

    # ─── Logo (columna izquierda, opcional) ───────────────────────────────
    columna_logo = ''
    if logo_url:
        columna_logo = (
            f'<td valign="top" style="vertical-align:top;padding-right:20px">'
            f'<img src="{escape(logo_url)}" alt="" '
            f'style="display:block;max-width:130px;height:auto;border:0" '
            f'width="130"></td>'
        )

    # ─── Wrapper ──────────────────────────────────────────────────────────
    html = (
        '<div style="margin-top:28px;padding-top:18px;'
        'border-top:1px solid #e8e8e8;font-family:-apple-system,'
        'BlinkMacSystemFont,\'Segoe UI\',Helvetica,Arial,sans-serif">'
        '<table cellpadding="0" cellspacing="0" border="0" role="presentation" '
        'style="border-collapse:collapse">'
        f'<tr>{columna_logo}{columna_derecha}</tr>'
        '</table></div>'
    )
    return html


def render_firma_texto(buzon) -> str:
    """Versión texto plano de la firma (para multipart/alternative)."""
    if not buzon or not getattr(buzon, 'firma_activa', True):
        return ''
    nombre   = (buzon.firma_nombre or '').strip()
    cargo    = (buzon.firma_cargo or '').strip()
    telefono = (buzon.firma_telefono or '').strip()
    email_v  = (buzon.firma_email_visible or buzon.email or '').strip()
    web      = (getattr(buzon, 'firma_web', '') or '').strip()

    lineas = ['--']
    if nombre:   lineas.append(nombre)
    if cargo:    lineas.append(cargo)
    if telefono: lineas.append(f'Tel: {telefono}')
    if email_v:  lineas.append(email_v)
    if web:      lineas.append(web)
    if len(lineas) == 1:
        return ''
    return '\n'.join(lineas)


@register.simple_tag
def firma_html(buzon):
    """Renderiza la firma HTML del buzón. Marcada como segura (escaping interno)."""
    return mark_safe(render_firma_html(buzon))


@register.simple_tag
def firma_texto(buzon):
    """Renderiza la firma en texto plano del buzón."""
    return render_firma_texto(buzon)
