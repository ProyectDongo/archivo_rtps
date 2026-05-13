"""
Crea la estructura realista de buzones + usuarios mapeando el Worker
de Cloudflare Email Routing.

Ejecuta:
    python manage.py seed_estructura
    python manage.py seed_estructura --password-default=ClaveTemp.2026!

Lo que crea:
  - 7 buzones @pietramonte.cl (vacíos, listos para importar correos reales)
  - 5 usuarios del portal con sus accesos M2M:

      soporte.dongo@gmail.com         → admin (ve todos)
      pventas.pietramonte@gmail.com   → aledezma, contacto
      vfinanzas.pietramonte@gmail.com → cobranza, cpietrasanta, vpietrasanta
      ral.pietramonte@gmail.com       → ralbornoz
      lvalverde.pietramonte@gmail.com → lvalverde

Si algún usuario YA existe, no se le toca el password (solo se ajusta el M2M).
"""
import secrets
import string
from django.core.management.base import BaseCommand

from correos.models import Buzon, UsuarioPortal


# Buzones reales
BUZONES = [
    'aledezma@pietramonte.cl',
    'cobranza@pietramonte.cl',
    'contacto@pietramonte.cl',
    'cpietrasanta@pietramonte.cl',
    'vpietrasanta@pietramonte.cl',
    'ralbornoz@pietramonte.cl',
    'lvalverde@pietramonte.cl',
]

# Mapeo gmail-empleado -> buzones que ve
ASIGNACIONES = {
    'soporte.dongo@gmail.com':         {'es_admin': True,  'buzones': []},
    'pventas.pietramonte@gmail.com':   {'es_admin': False, 'buzones': ['aledezma@pietramonte.cl', 'contacto@pietramonte.cl']},
    'vfinanzas.pietramonte@gmail.com': {'es_admin': False, 'buzones': ['cobranza@pietramonte.cl', 'cpietrasanta@pietramonte.cl', 'vpietrasanta@pietramonte.cl']},
    'ral.pietramonte@gmail.com':       {'es_admin': False, 'buzones': ['ralbornoz@pietramonte.cl']},
    'lvalverde.pietramonte@gmail.com': {'es_admin': False, 'buzones': ['lvalverde@pietramonte.cl']},
}


def _gen_password(n: int = 14) -> str:
    abc = ''.join(c for c in (string.ascii_letters + string.digits) if c not in 'lI1O0')
    extras = '!@#$%&*+-='
    return ''.join(secrets.choice(abc) for _ in range(n - 2)) + secrets.choice(extras) + secrets.choice(string.digits)


class Command(BaseCommand):
    help = 'Crea buzones y usuarios reales según el routing de Cloudflare'

    def add_arguments(self, parser):
        parser.add_argument('--password-default', type=str, default='',
                            help='Password para usuarios nuevos. Si se omite, se genera uno aleatorio por cada uno.')

    def handle(self, *args, **opts):
        # 1. Buzones
        for email in BUZONES:
            b, creado = Buzon.objects.get_or_create(email=email)
            self.stdout.write(f'  Buzon {"+" if creado else "."} {email}')

        self.stdout.write('')

        # 2. Usuarios
        passwords_generados = {}
        for u_email, cfg in ASIGNACIONES.items():
            usuario, creado = UsuarioPortal.objects.get_or_create(email=u_email)

            if creado:
                pwd = opts['password_default'] or _gen_password()
                usuario.set_password(pwd)
                passwords_generados[u_email] = pwd

            usuario.es_admin = cfg['es_admin']
            usuario.activo = True
            usuario.save()

            # Setear buzones (no aplica para admins)
            if not cfg['es_admin']:
                buzones_obj = Buzon.objects.filter(email__in=cfg['buzones'])
                usuario.buzones.set(buzones_obj)

            tag = 'admin' if cfg['es_admin'] else f'{len(cfg["buzones"])} buzones'
            self.stdout.write(f'  Usuario {"+" if creado else "."} {u_email} ({tag})')

        # 3. Reportar passwords generados (solo para nuevos)
        if passwords_generados:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'PASSWORDS NUEVOS — anotalos, no se vuelven a mostrar:'))
            for email, pwd in passwords_generados.items():
                self.stdout.write(f'  {email:<40}  {pwd}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Estructura lista.'))
