"""
Context processor que inyecta datos del usuario logueado en cada template
del área autenticada.

  - usuario_actual:    instancia UsuarioPortal o None
  - buzones_visibles:  queryset de buzones que el usuario puede ver
  - buzon_actual:      Buzon actualmente seleccionado (o None)

Reusa el cache `request._portal_user` que setea `_usuario_actual` en views.py
— sino estaríamos consultando UsuarioPortal dos veces por request (una en
la view, otra acá). Ahora es 1 query.
"""
from .models import Buzon


def portal(request):
    if not hasattr(request, 'session'):
        return {}

    # Importamos local para evitar ciclo de imports views → context_processors → views.
    from .views import _usuario_actual
    usuario = _usuario_actual(request)
    if not usuario:
        return {}

    visibles = usuario.buzones_visibles()
    buzon_actual = None
    bid = request.session.get('buzon_actual_id')
    if bid:
        try:
            buzon_actual = visibles.get(id=bid)
        except Buzon.DoesNotExist:
            pass

    return {
        'usuario_actual':    usuario,
        'buzones_visibles':  visibles,
        'buzon_actual':      buzon_actual,
    }
