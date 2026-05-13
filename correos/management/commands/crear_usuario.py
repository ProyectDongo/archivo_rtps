"""
Crea o actualiza un UsuarioPortal con password hasheado.

Uso:
  # Interactivo (pide password sin mostrarlo)
  python manage.py crear_usuario soporte.dongo@gmail.com

  # No interactivo (útil para scripts; el password queda en el shell history,
  # solo recomendado para setup inicial automatizado)
  python manage.py crear_usuario soporte.dongo@gmail.com --password=Cosa.Dificil-2026 --admin

  # Desactivar a alguien sin borrarlo
  python manage.py crear_usuario juan@gmail.com --desactivar
"""
import getpass
import secrets
import string
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from correos.models import UsuarioPortal


def _password_aleatorio(n: int = 14) -> str:
    """Genera un password legible (sin caracteres confusos como l, 1, O, 0)."""
    alfabeto = ''.join(c for c in (string.ascii_letters + string.digits)
                       if c not in 'lI1O0')
    extras = '!@#$%&*+-='
    cuerpo = ''.join(secrets.choice(alfabeto) for _ in range(n - 2))
    return cuerpo + secrets.choice(extras) + secrets.choice(string.digits)


class Command(BaseCommand):
    help = 'Crea o actualiza un usuario del portal con su contraseña.'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str)
        parser.add_argument('--password', type=str,
                            help='Si se omite, se pide interactivamente')
        parser.add_argument('--generar', action='store_true',
                            help='Genera un password aleatorio seguro y lo muestra')
        parser.add_argument('--admin', action='store_true',
                            help='Marca al usuario como administrador')
        parser.add_argument('--desactivar', action='store_true',
                            help='Marca al usuario como inactivo')

    def handle(self, *args, **opts):
        email = opts['email'].strip().lower()
        if '@' not in email or len(email) > 254:
            raise CommandError('Email inválido')

        usuario, creado = UsuarioPortal.objects.get_or_create(email=email)

        if opts['desactivar']:
            usuario.activo = False
            usuario.save(update_fields=['activo'])
            self.stdout.write(self.style.WARNING(f'[X] {email} marcado inactivo'))
            return

        # Determina el password a usar
        if opts['generar']:
            pwd = _password_aleatorio()
            self.stdout.write(self.style.SUCCESS(
                f'\n  PASSWORD GENERADO (anótalo, no se volverá a mostrar):\n  >>> {pwd}\n'
            ))
        elif opts['password']:
            pwd = opts['password']
        else:
            self.stdout.write(f'Configurando password para {email}')
            pwd = getpass.getpass('  Nueva contraseña: ')
            pwd2 = getpass.getpass('  Repite contraseña: ')
            if pwd != pwd2:
                raise CommandError('Las contraseñas no coinciden')
            if len(pwd) < 8:
                raise CommandError('La contraseña debe tener al menos 8 caracteres')

        # Validar contra AUTH_PASSWORD_VALIDATORS (mín 10, no común, no numérica,
        # no parecida al email)
        try:
            validate_password(pwd, user=usuario)
        except ValidationError as e:
            raise CommandError('Contraseña rechazada: ' + ' / '.join(e.messages))

        usuario.set_password(pwd)
        usuario.activo = True
        if opts['admin']:
            usuario.es_admin = True
        usuario.save()

        accion = 'creado' if creado else 'actualizado'
        rol = 'admin' if usuario.es_admin else 'empleado'
        self.stdout.write(self.style.SUCCESS(
            f'[OK] Usuario {accion}: {email} ({rol}, activo)'
        ))
