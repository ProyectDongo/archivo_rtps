"""
Anti-bot stack del form público de reservas.

Capas (del más liviano al más pesado), todas opcionales en dev:

  1. Cloudflare Turnstile  → server-side verification del token cf-turnstile.
  2. Captcha Fernet       → reusamos el de correos.captcha (challenge visual).
  3. Email verification   → código de 6 dígitos enviado al email del cliente.
  4. Blocklist desechables → bloquea mailinator, 10minutemail y ~150 más.
  5. Rate-limit por IP    → cache-based, 3 reservas/hora, 10 intentos/hora.
  6. Honeypot             → campo trampa invisible (name="website").

Cada función devuelve un valor booleano o lanza CaptchaError compatible.
La vista que las usa (taller.views.confirmar_reserva) compone las capas y
loguea el motivo del fallo en ReservaIntento para análisis posterior.
"""
from __future__ import annotations

import json
import logging
import secrets
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

from .disposable_domains import es_email_desechable

logger = logging.getLogger('archivo_pietramonte.anti_bot')


# ─── Cloudflare Turnstile ──────────────────────────────────────────────────
TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


def verify_turnstile(token: str, ip: str = '') -> bool:
    """
    Valida el token cf-turnstile contra el endpoint de Cloudflare.

    Si TURNSTILE_SECRET_KEY no está seteado (dev local), devuelve True
    para no bloquear desarrollo. En producción siempre debe estar seteado.
    """
    secret = getattr(settings, 'TURNSTILE_SECRET_KEY', '')
    if not secret:
        logger.debug('TURNSTILE_SECRET_KEY no seteado — bypass anti-bot.')
        return True
    if not token:
        return False

    payload = urlencode({
        'secret': secret,
        'response': token,
        'remoteip': ip or '',
    }).encode('utf-8')

    try:
        req = Request(
            TURNSTILE_VERIFY_URL,
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return bool(data.get('success', False))
    except Exception:
        logger.exception('Error verificando Turnstile')
        return False


# ─── Email verification (código 6 dígitos por cache) ───────────────────────
EMAIL_VERIFY_TTL_SEG = 15 * 60        # el código vive 15 minutos
EMAIL_VERIFY_PREFIX  = 'agendar:emailverify:'


def generar_codigo_email(email: str) -> str:
    """
    Genera un código de 6 dígitos atado al email (case-insensitive) y lo guarda
    en cache. Sobreescribe cualquier código anterior del mismo email.
    """
    code = '{:06d}'.format(secrets.randbelow(10**6))
    cache.set(EMAIL_VERIFY_PREFIX + email.lower().strip(), code, EMAIL_VERIFY_TTL_SEG)
    return code


def verificar_codigo_email(email: str, code: str) -> bool:
    """
    True si el código matchea el guardado para este email. Lo borra al validar
    para evitar reusos. False si nunca se generó o expiró.
    """
    key = EMAIL_VERIFY_PREFIX + email.lower().strip()
    expected = cache.get(key)
    if not expected:
        return False
    code_clean = (code or '').strip().replace(' ', '').replace('-', '')
    if not secrets.compare_digest(str(expected), code_clean):
        return False
    cache.delete(key)
    return True


def hay_codigo_pendiente(email: str) -> bool:
    """True si ya hay un código vigente para este email (no regenerar tan rápido)."""
    return cache.get(EMAIL_VERIFY_PREFIX + email.lower().strip()) is not None


# ─── Blocklist de emails desechables ───────────────────────────────────────
def _extra_domains() -> frozenset[str]:
    """
    Dominios extras configurables via env (CSV: DISPOSABLE_DOMAINS_EXTRA).
    Se pasan a `es_email_desechable` como complemento de la lista base.
    """
    raw = getattr(settings, 'DISPOSABLE_DOMAINS_EXTRA', None)
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(d.lower().strip() for d in raw if d.strip())
    if isinstance(raw, str):
        return frozenset(d.lower().strip() for d in raw.split(',') if d.strip())
    return frozenset()


def email_es_desechable(email: str) -> bool:
    return es_email_desechable(email, extra=_extra_domains())


# ─── Rate-limit por IP-hash ────────────────────────────────────────────────
RL_VENTANA_SEG_RESERVAS = 60 * 60     # 1 hora
RL_VENTANA_SEG_INTENTOS = 60 * 60
RL_MAX_RESERVAS = 3                   # 3 reservas exitosas por IP/hora
RL_MAX_INTENTOS = 10                  # 10 intentos (incluyendo fallos) por IP/hora

_RL_KEY_RESERVAS = 'agendar:rl:res:{ip}'
_RL_KEY_INTENTOS = 'agendar:rl:int:{ip}'


def rl_intento(ip_hash: str) -> int:
    """Suma 1 al contador de intentos. Devuelve el nuevo total."""
    key = _RL_KEY_INTENTOS.format(ip=ip_hash)
    n = (cache.get(key) or 0) + 1
    cache.set(key, n, RL_VENTANA_SEG_INTENTOS)
    return n


def rl_reserva(ip_hash: str) -> int:
    """Suma 1 al contador de reservas exitosas. Devuelve el nuevo total."""
    key = _RL_KEY_RESERVAS.format(ip=ip_hash)
    n = (cache.get(key) or 0) + 1
    cache.set(key, n, RL_VENTANA_SEG_RESERVAS)
    return n


def rl_bloqueado_intentos(ip_hash: str) -> bool:
    return (cache.get(_RL_KEY_INTENTOS.format(ip=ip_hash)) or 0) >= RL_MAX_INTENTOS


def rl_bloqueado_reservas(ip_hash: str) -> bool:
    return (cache.get(_RL_KEY_RESERVAS.format(ip=ip_hash)) or 0) >= RL_MAX_RESERVAS


# ─── Honeypot ──────────────────────────────────────────────────────────────
HONEYPOT_FIELD = 'website'        # nombre del campo trampa en el form


def honeypot_lleno(post_data) -> bool:
    """True si el campo trampa tiene cualquier contenido."""
    return bool((post_data.get(HONEYPOT_FIELD) or '').strip())
