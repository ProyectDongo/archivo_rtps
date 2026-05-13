"""
Captcha propio basado en íconos SVG (estilo Lucide) — sin dependencias externas.

v3 (2026-05-06): el payload ahora va CIFRADO con Fernet, no solo firmado HMAC.
Antes la respuesta correcta viajaba en plaintext base64 dentro del token,
así que cualquier bot que decodificara el token tenía la solución gratis.
Ahora el token es opaco para el cliente.

Diseño:
  - El servidor genera un challenge (categoría + 9 íconos donde N son correctos).
  - Empaqueta los índices correctos + nonce + timestamp en JSON.
  - **Cifra el JSON con Fernet** (AES-128-CBC + HMAC-SHA256, derivado de SECRET_KEY).
  - Envía: lista de íconos {nombre, svg} + token cifrado.
  - El cliente envía: índices seleccionados + token tal cual lo recibió.
  - El servidor desencripta (lo que valida firma + TTL en una sola operación) y
    compara la selección contra los índices correctos.

Beneficios:
  - El cliente NO puede descifrar el token sin la clave del server.
  - Un bot que parsea el HTML no puede leer la respuesta correcta del token.
  - El TTL de 3 min está incrustado en Fernet (atributo `ttl` en decrypt).
  - Cero estado en servidor (no hace falta cache ni sesión).
  - Sin dependencias externas: cryptography ya viene como dep transitiva de Django.

Lo que el captcha NO previene (defensa en profundidad lo cubre):
  - Bot avanzado que clasifique los SVG (rate-limit + honeypot + tiempo mínimo).
  - Esto es un filtro anti-spam, no anti-credential-stuffing. Para eso está 2FA.
"""

import base64
import hashlib
import json
import secrets
import time
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


# ─── Íconos SVG (paths Lucide MIT, viewBox 24×24, stroke 1.8 sin fill) ─────
# Curados para que sean inconfundibles a 32-40 px. Si agregás más, mantené
# el estilo: solo `<path>` / `<circle>` / `<polygon>`, sin `style=`, sin
# `<defs>`, sin `<g>`. Eso queda envuelto en `<svg>` por el template.
ICONS: dict[str, str] = {
    # ── Vehículos ──────────────────────────────────────────────────────
    'car':
        '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9L18.4 5.5'
        'c-.6-1.1-1.7-1.7-2.9-1.7H8.5c-1.2 0-2.3.6-2.9 1.7L3.5 11.1'
        'C2.7 11.3 2 12.1 2 13v3c0 .6.4 1 1 1h2"/>'
        '<path d="M2 11h20"/>'
        '<circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/>',
    'truck':
        '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/>'
        '<path d="M15 18H9"/>'
        '<path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35'
        'A1 1 0 0 0 17.52 8H14"/>'
        '<circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
    'bus':
        '<path d="M8 6v6"/><path d="M15 6v6"/><path d="M2 12h19.6"/>'
        '<path d="M18 18h3s.5-1.7.8-2.8c.1-.4.2-.8.2-1.2 0-.4-.1-.8-.2-1.2'
        'l-1.4-5C20.1 6.8 19.1 6 18 6H4a2 2 0 0 0-2 2v10h3"/>'
        '<circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
    'bike':
        '<circle cx="18.5" cy="17.5" r="3.5"/>'
        '<circle cx="5.5" cy="17.5" r="3.5"/>'
        '<circle cx="15" cy="5" r="1"/>'
        '<path d="M12 17.5V14l-3-3 4-3 2 3h2"/>',
    'motorcycle':
        '<circle cx="6" cy="17" r="3"/><circle cx="19" cy="17" r="3"/>'
        '<path d="M8 14h6l-3-9h2l5 6"/>',
    'plane':
        '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8'
        ' 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2'
        ' 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>',
    'ship':
        '<path d="M2 21c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2c1.3 0 1.9.5 2.5 1"/>'
        '<path d="M19.38 20A11.6 11.6 0 0 0 21 14l-9-4-9 4c0 2.9.94 5.34 2.81 7.76"/>'
        '<path d="M19 13V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6"/>'
        '<path d="M12 10v4"/><path d="M12 2v3"/>',

    # ── Herramientas mecánicas ────────────────────────────────────────
    'wrench':
        '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77'
        'a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91'
        'a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    'hammer':
        '<path d="m15 12-8.5 8.5c-.83.83-2.17.83-3 0a2.12 2.12 0 0 1 0-3L12 9"/>'
        '<path d="M17.64 15 22 10.64"/>'
        '<path d="m20.91 11.7-1.25-1.25c-.6-.6-.93-1.4-.93-2.25v-.86L16.01 4.6'
        'a5.56 5.56 0 0 0-3.94-1.64H9l.92.82A6.18 6.18 0 0 1 12 8.4v1.56l2 2'
        'h2.47l2.26 1.91"/>',
    'gear':
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25'
        'a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73'
        'l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73'
        'l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20'
        'a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25'
        'a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73'
        'l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73'
        'l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25'
        'a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>',
    'screwdriver':
        '<path d="M15 4V2"/><path d="M15 16v-2"/>'
        '<path d="M8 9h2"/><path d="M20 9h2"/>'
        '<path d="M17.8 11.8 19 13"/><path d="M15 9h0"/>'
        '<path d="M17.8 6.2 19 5"/>'
        '<path d="m3 21 9-9"/><path d="M12.2 6.2 11 5"/>',

    # ── Naturaleza ─────────────────────────────────────────────────────
    'tree':
        '<path d="M12 22V13"/>'
        '<path d="M9 22h6"/>'
        '<path d="M17 14a5 5 0 1 0-10 0 5 5 0 0 0 5 5 5 5 0 0 0 5-5z"/>'
        '<circle cx="12" cy="9" r="5"/>',
    'flower':
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M12 9V2"/><path d="M12 22v-7"/>'
        '<path d="M9 12H2"/><path d="M22 12h-7"/>'
        '<path d="m4.93 4.93 5.07 5.07"/><path d="m14 14 5.07 5.07"/>'
        '<path d="m19.07 4.93-5.07 5.07"/><path d="m4.93 19.07 5.07-5.07"/>',
    'sun':
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2"/><path d="M12 20v2"/>'
        '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>'
        '<path d="M2 12h2"/><path d="M20 12h2"/>'
        '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    'moon':
        '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    'cloud':
        '<path d="M17.5 19a4.5 4.5 0 1 0 0-9 6 6 0 0 0-11.69 1.5A4 4 0 0 0 7 19h10.5z"/>',
    'leaf':
        '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19.5 2c1 2 2 4.18 2 8'
        ' 0 5.5-4.78 10-10 10Z"/>'
        '<path d="M2 21c0-3 1.85-5.36 5.08-6"/>',
    'mountain':
        '<path d="m8 3 4 8 5-5 5 15H2L8 3z"/>',

    # ── Cotidianos / objetos ──────────────────────────────────────────
    'home':
        '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
        '<polyline points="9 22 9 12 15 12 15 22"/>',
    'coffee':
        '<path d="M17 8h1a4 4 0 1 1 0 8h-1"/>'
        '<path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"/>'
        '<line x1="6" x2="6" y1="2" y2="4"/>'
        '<line x1="10" x2="10" y1="2" y2="4"/>'
        '<line x1="14" x2="14" y1="2" y2="4"/>',
    'music':
        '<path d="M9 18V5l12-2v13"/>'
        '<circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    'heart':
        '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2'
        ' -1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    'star':
        '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02'
        ' 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26"/>',
    'gift':
        '<rect x="3" y="8" width="18" height="4" rx="1"/>'
        '<path d="M12 8v13"/>'
        '<path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/>'
        '<path d="M7.5 8a2.5 2.5 0 0 1 0-5C9.5 3 12 5 12 8c0-3 2.5-5 4.5-5'
        'a2.5 2.5 0 0 1 0 5"/>',
    'camera':
        '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9'
        'a2 2 0 0 0-2-2h-3l-2.5-3z"/>'
        '<circle cx="12" cy="13" r="3"/>',
    'book':
        '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
    'phone':
        '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07'
        ' 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2'
        'h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11'
        'L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7'
        ' A2 2 0 0 1 22 16.92z"/>',
}


# ─── Categorías disponibles ────────────────────────────────────────────────
CHALLENGES: dict[str, dict] = {
    'vehiculos': {
        'pregunta': 'Selecciona todos los vehículos',
        'correctos':   ['car', 'truck', 'bus', 'bike', 'motorcycle', 'plane', 'ship'],
        'distractores': ['home', 'tree', 'coffee', 'music', 'sun', 'moon',
                         'heart', 'star', 'gift', 'camera', 'book', 'phone',
                         'flower', 'cloud', 'leaf', 'mountain'],
    },
    'herramientas': {
        'pregunta': 'Selecciona las herramientas mecánicas',
        'correctos':   ['wrench', 'hammer', 'gear', 'screwdriver'],
        'distractores': ['home', 'tree', 'coffee', 'music', 'sun', 'moon',
                         'heart', 'star', 'gift', 'camera', 'book', 'phone',
                         'car', 'flower', 'cloud', 'leaf'],
    },
    'naturaleza': {
        'pregunta': 'Selecciona elementos de la naturaleza',
        'correctos':   ['tree', 'sun', 'moon', 'flower', 'cloud', 'leaf', 'mountain', 'star'],
        'distractores': ['car', 'truck', 'wrench', 'hammer', 'home',
                         'coffee', 'music', 'gift', 'camera', 'book', 'phone',
                         'gear', 'bike', 'plane'],
    },
}

GRID_SIZE = 9
CORRECT_RANGE = (3, 5)
TTL_SEGUNDOS = 180


# ─── Crypto: Fernet derivado de SECRET_KEY ─────────────────────────────────
def _fernet_key() -> bytes:
    """Deriva una clave Fernet de 32 bytes desde SECRET_KEY (estable, no rota)."""
    digest = hashlib.sha256(
        ('captcha-v3::' + settings.SECRET_KEY).encode('utf-8')
    ).digest()
    return base64.urlsafe_b64encode(digest)


def _crypto() -> Fernet:
    return Fernet(_fernet_key())


# ─── Generación ────────────────────────────────────────────────────────────
def generar_challenge(categoria: str | None = None) -> dict:
    """
    Devuelve un challenge listo para renderizar:
      {
        'pregunta':  'Selecciona todos los vehículos',
        'categoria': 'vehiculos',
        'celdas':    [{'nombre': 'car', 'svg': '<path .../>...'}, ...],
        'token':     '<fernet ciphertext>',
      }
    """
    if categoria is None or categoria not in CHALLENGES:
        categoria = secrets.choice(list(CHALLENGES.keys()))

    bloque = CHALLENGES[categoria]
    n_correctos = secrets.choice(range(CORRECT_RANGE[0], CORRECT_RANGE[1] + 1))
    n_correctos = min(n_correctos, len(bloque['correctos']), GRID_SIZE - 2)

    rng = secrets.SystemRandom()
    correctos_nombres = rng.sample(bloque['correctos'], n_correctos)
    n_distractores = GRID_SIZE - n_correctos
    distractores_nombres = rng.sample(bloque['distractores'], n_distractores)

    nombres = correctos_nombres + distractores_nombres
    rng.shuffle(nombres)

    indices_correctos = sorted([i for i, n in enumerate(nombres) if n in correctos_nombres])

    payload = json.dumps({
        'c': categoria,
        'i': indices_correctos,
        'n': secrets.token_hex(8),
    }, separators=(',', ':')).encode('utf-8')

    # Fernet incluye AES-128-CBC + HMAC-SHA256 + timestamp; lo que nosotros
    # validamos con el ttl en `decrypt`. No necesitamos guardar el timestamp
    # nosotros mismos.
    token = _crypto().encrypt(payload).decode('ascii')

    return {
        'pregunta':  bloque['pregunta'],
        'categoria': categoria,
        'celdas': [{'nombre': n, 'svg': ICONS[n]} for n in nombres],
        'token':     token,
    }


# ─── Verificación ──────────────────────────────────────────────────────────
class CaptchaError(Exception):
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def verificar(token: str, indices_seleccionados: Iterable[int]) -> str:
    """
    Valida el token + selección. Devuelve la categoría del challenge si OK.
    Lanza CaptchaError si algo falla.
    """
    if not token:
        raise CaptchaError('token_ausente')

    try:
        payload = _crypto().decrypt(token.encode('ascii'), ttl=TTL_SEGUNDOS)
    except InvalidToken:
        # Cubre: firma inválida, token corrupto, expirado.
        raise CaptchaError('token_invalido_o_expirado')
    except Exception:
        raise CaptchaError('token_malformado')

    try:
        data = json.loads(payload.decode('utf-8'))
    except Exception:
        raise CaptchaError('payload_no_json')

    correctos = set(int(x) for x in data.get('i', []))
    try:
        seleccion = set(int(x) for x in indices_seleccionados)
    except (TypeError, ValueError):
        raise CaptchaError('seleccion_invalida')

    if any(i < 0 or i >= GRID_SIZE for i in seleccion):
        raise CaptchaError('seleccion_fuera_rango')

    if seleccion != correctos:
        raise CaptchaError('respuesta_incorrecta')

    return data.get('c', 'desconocida')
