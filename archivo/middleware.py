"""
Middleware de seguridad:
  - CSPMiddleware: añade Content-Security-Policy. Estricto por defecto,
    relajado en rutas de admin (necesitan 'unsafe-inline' para sus widgets).
  - AdminLoginRateLimitMiddleware: limita intentos fallidos al admin Django
    (8 fallos / 15 min / IP → 429 con Retry-After).
"""
import hashlib

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

# ─── CSP ───────────────────────────────────────────────────────────────────

_CSP_STRICT = (
    "default-src 'self'; "
    "script-src 'self' https://challenges.cloudflare.com; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-src https://challenges.cloudflare.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

_CSP_PORTAL = (
    "default-src 'self'; "
    "script-src 'self' https://challenges.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https: http:; "
    "connect-src 'self'; "
    "frame-src https://challenges.cloudflare.com 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

_CSP_ADMIN = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self';"
)


class CSPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        admin_path = '/' + settings.ADMIN_URL_PATH
        # No sobreescribir si la vista ya puso su propio CSP (ej: adjunto inline).
        if 'Content-Security-Policy' not in response:
            if request.path.startswith(admin_path):
                response['Content-Security-Policy'] = _CSP_ADMIN
            elif request.path.startswith('/intranet/'):
                response['Content-Security-Policy'] = _CSP_PORTAL
            else:
                response['Content-Security-Policy'] = _CSP_STRICT
        return response


# ─── Admin login rate-limit ─────────────────────────────────────────────────

_ADMIN_RL_MAX     = 8       # intentos fallidos máximos
_ADMIN_RL_WINDOW  = 15 * 60 # ventana en segundos
_ADMIN_RL_RETRY   = 60 * 15 # Retry-After en segundos


def _ip_hash(request) -> str:
    ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
    return hashlib.sha256(ip.encode()).hexdigest()[:24]


class AdminLoginRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        admin_login = '/' + settings.ADMIN_URL_PATH + 'login/'

        if request.path == admin_login and request.method == 'POST':
            key = f'admin_rl:{_ip_hash(request)}'
            hits = cache.get(key, 0)
            if hits >= _ADMIN_RL_MAX:
                resp = HttpResponse(
                    'Demasiados intentos. Intenta de nuevo en 15 minutos.',
                    status=429,
                    content_type='text/plain',
                )
                resp['Retry-After'] = str(_ADMIN_RL_RETRY)
                return resp

            response = self.get_response(request)

            # Si falló (admin devuelve 200 con form, no 302)
            if response.status_code == 200 and 'errornote' in response.content.decode('utf-8', errors='ignore'):
                cache.set(key, hits + 1, _ADMIN_RL_WINDOW)
            elif response.status_code == 302:
                # Login exitoso → resetear contador
                cache.delete(key)

            return response

        return self.get_response(request)
