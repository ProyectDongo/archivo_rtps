"""
Rate limiting para endpoints autenticados del portal.

Diseño:
  - Ventana fija por minuto (`bucket = int(time() // 60)`). Más simple que
    sliding window y suficiente para el threat model: queremos cortar abuso,
    no microsegundos de precisión.
  - Cuenta por (scope, identidad), donde identidad = email del usuario portal
    o, si no hay sesión, IP hash (para cubrir endpoints semipúblicos).
  - Backend = `django.core.cache`. Con REDIS_URL configurado, es consistente
    entre gunicorn workers; con LocMemCache, es per-worker (degradación
    graceful, ver settings.py).
  - Header `Retry-After` en el 429 para que clientes (o atacantes humanos)
    sepan cuánto esperar.

Uso:
    @throttle_user('inbox', per_minute=60)
    def inbox_view(request):
        ...

    @throttle_ip('public', per_minute=30)
    def public_endpoint(request):
        ...
"""
from __future__ import annotations

import logging
from functools import wraps
from time import time

from django.core.cache import cache
from django.http import HttpResponse


logger = logging.getLogger('correos.throttle')


def _bucket() -> int:
    """Ventana de 60 segundos alineada al reloj."""
    return int(time() // 60)


def _segs_para_proximo_bucket() -> int:
    """Cuántos segundos faltan hasta que rote la ventana."""
    return 60 - (int(time()) % 60)


def _bloqueado(key: str, limite: int) -> tuple[bool, int]:
    """
    Incrementa el contador de la key y devuelve (bloqueado, count_actual).
    El primer hit del bucket inicializa con TTL 65s — un poco más que la
    ventana, para que la key viva justo lo necesario y se limpie sola.
    """
    count = cache.get(key, 0) + 1
    cache.set(key, count, 65)
    return (count > limite, count)


def _respuesta_429(retry_after: int) -> HttpResponse:
    resp = HttpResponse(
        'Demasiadas peticiones. Esperá un momento antes de volver a intentar.',
        status=429,
        content_type='text/plain; charset=utf-8',
    )
    resp['Retry-After'] = str(retry_after)
    return resp


def throttle_user(scope: str, per_minute: int):
    """
    Limita por usuario portal (sesión). Si no hay sesión, deja pasar
    (otros decoradores manejan auth — esto NO es un sustituto de @login).

    `scope` separa contadores: el rate limit del inbox NO se mezcla con el
    de adjuntos. Así un usuario puede bajar muchos adjuntos sin bloquear su
    navegación del inbox.
    """
    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            email = (request.session.get('usuario_email') or '').strip().lower()
            if email:
                key = f'throttle:{scope}:u:{email}:{_bucket()}'
                bloqueado, count = _bloqueado(key, per_minute)
                if bloqueado:
                    logger.warning(
                        'Rate-limit alcanzado scope=%s user=%s count=%d limit=%d',
                        scope, email, count, per_minute,
                    )
                    return _respuesta_429(_segs_para_proximo_bucket())
            return view(request, *args, **kwargs)
        return wrapper
    return deco


def throttle_ip(scope: str, per_minute: int):
    """
    Limita por IP hasheada. Útil para endpoints públicos sin login (ej.
    healthcheck o un endpoint AJAX abierto).
    """
    from .models import hash_ip

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            from .views import _get_ip
            ip_h = hash_ip(_get_ip(request))
            key = f'throttle:{scope}:ip:{ip_h}:{_bucket()}'
            bloqueado, count = _bloqueado(key, per_minute)
            if bloqueado:
                logger.warning(
                    'Rate-limit IP scope=%s ip_hash=%s count=%d limit=%d',
                    scope, ip_h[:12], count, per_minute,
                )
                return _respuesta_429(_segs_para_proximo_bucket())
            return view(request, *args, **kwargs)
        return wrapper
    return deco
