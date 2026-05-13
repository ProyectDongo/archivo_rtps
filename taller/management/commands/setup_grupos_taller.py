"""
Crea (o sincroniza) los 4 grupos de permisos del taller:

  - Personal Agenda       → ver/editar Reservas + ver Catálogo y Bloqueos
  - Personal Cotizaciones → (Fase B) ver/editar Cotizaciones
  - Personal Catálogo     → CRUD del Catálogo (precios, fotos, items)
  - Personal Completo     → todo lo anterior

El admin asigna usuarios a grupos desde /admin-…/auth/user/. Marcá también
"Es staff" en el usuario para que pueda entrar al admin (no marqués
"superusuario" salvo que quieras darle TODO el sistema).

Uso:
    python manage.py setup_grupos_taller
    python manage.py setup_grupos_taller --reset    # borra y recrea
"""
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from taller.models import BloqueoCalendario, ItemCatalogo, Reserva, ReservaIntento


# Mapping grupo → modelos y permisos
# 'view' / 'add' / 'change' / 'delete' siguen la convención de Django.
GRUPOS = {
    'Personal Agenda': {
        'descripcion': 'Recepcionista / secretaria — gestiona reservas y ve el catálogo.',
        'permisos': [
            (Reserva,            ['view', 'change']),
            (ItemCatalogo,       ['view']),
            (BloqueoCalendario,  ['view']),
            (ReservaIntento,     ['view']),
        ],
    },
    'Personal Cotizaciones': {
        'descripcion': 'Vendedor de repuestos — gestiona cotizaciones (Fase B). Por ahora solo lectura del catálogo.',
        'permisos': [
            (ItemCatalogo,       ['view']),
            # Cuando se implemente el modelo Cotizacion, agregar acá:
            # (Cotizacion, ['view', 'add', 'change']),
        ],
    },
    'Personal Catálogo': {
        'descripcion': 'Encargado de inventario — administra precios, fotos y disponibilidad del catálogo.',
        'permisos': [
            (ItemCatalogo,       ['view', 'add', 'change', 'delete']),
            (Reserva,            ['view']),
        ],
    },
    'Personal Completo': {
        'descripcion': 'Jefe de taller — todo lo de Agenda, Cotizaciones y Catálogo combinado.',
        'permisos': [
            (Reserva,            ['view', 'add', 'change', 'delete']),
            (ItemCatalogo,       ['view', 'add', 'change', 'delete']),
            (BloqueoCalendario,  ['view', 'add', 'change', 'delete']),
            (ReservaIntento,     ['view']),
        ],
    },
}


def _permisos_para(modelo, acciones):
    """Devuelve los Permission objects para un modelo + lista de acciones."""
    ct = ContentType.objects.get_for_model(modelo)
    nombres = [f'{a}_{ct.model}' for a in acciones]
    return list(Permission.objects.filter(content_type=ct, codename__in=nombres))


class Command(BaseCommand):
    help = 'Crea/sincroniza los 4 grupos de permisos del taller.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Borra los grupos antes de recrearlos (limpia membresías existentes).',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['reset']:
            n = Group.objects.filter(name__in=GRUPOS.keys()).delete()[0]
            self.stdout.write(self.style.WARNING(f'  Borrados {n} grupo(s) previos.'))

        for nombre, conf in GRUPOS.items():
            grupo, creado = Group.objects.get_or_create(name=nombre)
            verbo = 'Creado' if creado else 'Actualizado'

            permisos = []
            for modelo, acciones in conf['permisos']:
                permisos.extend(_permisos_para(modelo, acciones))

            grupo.permissions.set(permisos)
            self.stdout.write(f'  {verbo}: {nombre} ({len(permisos)} permisos) — {conf["descripcion"]}')

        self.stdout.write(self.style.SUCCESS(
            f'\nGrupos sincronizados. Asigná usuarios desde /admin-.../auth/user/'
        ))
