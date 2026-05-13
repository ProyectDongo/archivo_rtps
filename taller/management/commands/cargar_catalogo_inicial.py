"""
Carga el catálogo inicial del taller con 15 items de muestra (10 servicios +
5 repuestos). Precios y duraciones son referenciales del mercado chileno; el
admin los ajusta desde /admin-…/taller/itemcatalogo/.

Uso:
    python manage.py cargar_catalogo_inicial          # crea solo si no existen
    python manage.py cargar_catalogo_inicial --reset  # borra y recarga (¡cuidado!)

Las imágenes NO se descargan automáticamente — los items quedan con su ícono
Lucide como visual hasta que el admin suba fotos reales del taller (admin → Item
→ campo Imagen → guardar).
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from taller.models import ItemCatalogo


# ─── Catálogo de muestra ───────────────────────────────────────────────────
# Estos items pretenden cubrir el espectro de un taller mecánico chileno
# para autos personales y camionetas/4x4 (mercado minería). Precios al alza
# 2026 — el admin los ajusta a sus márgenes reales.
ITEMS_INICIALES = [
    # ─── Servicios ─────────────────────────────────────────────────────
    {
        'tipo': 'servicio', 'categoria': 'mantencion',
        'nombre': 'Cambio de aceite + filtro',
        'descripcion': 'Cambio de aceite del motor + filtro de aceite. Incluye revisión visual general (niveles, luces, neumáticos).',
        'precio_referencia_clp': 35000, 'duracion_min': 30,
        'icono_lucide': 'droplet', 'destacado': True, 'orden': 10,
    },
    {
        'tipo': 'servicio', 'categoria': 'neumaticos',
        'nombre': 'Alineación y balanceo',
        'descripcion': 'Alineación computarizada de las 4 ruedas + balanceo. Mejora rendimiento y vida útil de los neumáticos.',
        'precio_referencia_clp': 30000, 'duracion_min': 60,
        'icono_lucide': 'gauge', 'destacado': False, 'orden': 20,
    },
    {
        'tipo': 'servicio', 'categoria': 'frenos',
        'nombre': 'Cambio de pastillas de freno',
        'descripcion': 'Cambio de pastillas delanteras + revisión de discos y líquido de frenos. Si los discos están bajo medida, te avisamos antes de cambiarlos.',
        'precio_referencia_clp': 45000, 'duracion_min': 90,
        'icono_lucide': 'disc', 'destacado': True, 'orden': 30,
    },
    {
        'tipo': 'servicio', 'categoria': 'revision_tecnica',
        'nombre': 'Pre-revisión técnica',
        'descripcion': 'Revisión completa para asegurar que tu vehículo aprueba la planta de revisión técnica: luces, frenos, dirección, escape, neumáticos.',
        'precio_referencia_clp': 25000, 'duracion_min': 60,
        'icono_lucide': 'clipboard-check', 'destacado': False, 'orden': 40,
    },
    {
        'tipo': 'servicio', 'categoria': 'diagnostico',
        'nombre': 'Diagnóstico computacional (escaneo)',
        'descripcion': 'Lectura de códigos de falla con scanner OBD-II. Útil cuando se enciende la luz "check engine" o sentís algo raro en el comportamiento del motor.',
        'precio_referencia_clp': 15000, 'duracion_min': 45,
        'icono_lucide': 'cpu', 'destacado': False, 'orden': 50,
    },
    {
        'tipo': 'servicio', 'categoria': 'electrico',
        'nombre': 'Cambio de batería',
        'descripcion': 'Cambio de batería (incluye prueba de alternador y partidor). Disposición de la batería antigua por nuestra cuenta.',
        'precio_referencia_clp': 20000, 'duracion_min': 30,
        'icono_lucide': 'battery', 'destacado': False, 'orden': 60,
    },
    {
        'tipo': 'servicio', 'categoria': 'aire',
        'nombre': 'Recarga de aire acondicionado',
        'descripcion': 'Carga de gas refrigerante R134a o R1234yf según tu vehículo. Incluye chequeo de fugas con luz UV.',
        'precio_referencia_clp': 40000, 'duracion_min': 60,
        'icono_lucide': 'wind', 'destacado': False, 'orden': 70,
    },
    {
        'tipo': 'servicio', 'categoria': 'motor',
        'nombre': 'Cambio de correa de distribución',
        'descripcion': 'Cambio de correa de distribución + tensores + bomba de agua si corresponde. Servicio crítico — su rotura puede dañar el motor severamente.',
        'precio_referencia_clp': 180000, 'duracion_min': 240,
        'icono_lucide': 'cog', 'destacado': False, 'orden': 80,
    },
    {
        'tipo': 'servicio', 'categoria': 'camionetas_4x4',
        'nombre': 'Mantención completa camioneta 4x4',
        'descripcion': 'Servicio integral para camionetas de uso minero/off-road: aceite motor + caja + diferenciales, filtros, frenos, suspensión y revisión 4x4.',
        'precio_referencia_clp': 250000, 'duracion_min': 240,
        'icono_lucide': 'truck', 'destacado': True, 'orden': 90,
    },
    {
        'tipo': 'servicio', 'categoria': 'detailing',
        'nombre': 'Lavado premium + encerado',
        'descripcion': 'Lavado exterior detallado, aspirado interior, encerado de carrocería y abrillantado de neumáticos. 2 horas de trabajo.',
        'precio_referencia_clp': 25000, 'duracion_min': 90,
        'icono_lucide': 'sparkles', 'destacado': False, 'orden': 100,
    },

    # ─── Repuestos ─────────────────────────────────────────────────────
    {
        'tipo': 'repuesto', 'categoria': 'rep_aceites',
        'nombre': 'Aceite sintético 5W30 (4 litros)',
        'descripcion': 'Aceite sintético premium 5W30 SP, ideal para motores modernos a gasolina y diesel. Bidón de 4 litros.',
        'precio_referencia_clp': 28000, 'duracion_min': 0,
        'marca_repuesto': 'premium', 'icono_lucide': 'droplet', 'orden': 200,
    },
    {
        'tipo': 'repuesto', 'categoria': 'rep_filtros',
        'nombre': 'Filtro de aire universal',
        'descripcion': 'Filtro de aire de motor para autos pequeños y medianos. Compatible con la mayoría de marcas asiáticas.',
        'precio_referencia_clp': 8500, 'duracion_min': 0,
        'marca_repuesto': 'generico', 'icono_lucide': 'filter', 'orden': 210,
    },
    {
        'tipo': 'repuesto', 'categoria': 'rep_frenos',
        'nombre': 'Pastillas de freno Toyota Hilux',
        'descripcion': 'Juego de pastillas de freno delanteras OEM Toyota para Hilux 2016 en adelante. Garantía 1 año.',
        'precio_referencia_clp': 35000, 'duracion_min': 0,
        'marca_repuesto': 'oem', 'icono_lucide': 'disc', 'destacado': True, 'orden': 220,
    },
    {
        'tipo': 'repuesto', 'categoria': 'rep_electrico',
        'nombre': 'Batería 12V 75Ah libre mantención',
        'descripcion': 'Batería de plomo-ácido sellada, 12V 75Ah, 720 CCA. Compatible con sedanes y camionetas medianas. Incluye instalación gratis.',
        'precio_referencia_clp': 75000, 'duracion_min': 0,
        'marca_repuesto': 'premium', 'icono_lucide': 'battery', 'orden': 230,
    },
    {
        'tipo': 'repuesto', 'categoria': 'rep_camionetas',
        'nombre': 'Kit ruedas off-road camioneta (set 4)',
        'descripcion': 'Set completo de 4 neumáticos all-terrain 265/65 R17 para camionetas 4x4. Ideal para uso minería + caminos de tierra. Incluye montaje, balanceo y alineación.',
        'precio_referencia_clp': 480000, 'duracion_min': 0,
        'marca_repuesto': 'premium', 'icono_lucide': 'circle-dot', 'destacado': True, 'orden': 240,
    },
]


class Command(BaseCommand):
    help = 'Carga el catálogo inicial del taller (10 servicios + 5 repuestos).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Borra TODO el catálogo antes de cargar. ¡Cuidado en producción!',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['reset']:
            n_borr = ItemCatalogo.objects.all().delete()[0]
            self.stdout.write(self.style.WARNING(
                f'  Borrados {n_borr} items previos del catálogo.'))

        creados, ya_existentes = 0, 0
        for data in ITEMS_INICIALES:
            obj, creado = ItemCatalogo.objects.get_or_create(
                nombre=data['nombre'],
                defaults=data,
            )
            if creado:
                creados += 1
                self.stdout.write(f'  + {obj.nombre}')
            else:
                ya_existentes += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nCatálogo cargado:\n'
            f'  Creados:        {creados}\n'
            f'  Ya existentes:  {ya_existentes}\n'
            f'  Total en BD:    {ItemCatalogo.objects.count()}\n'
            f'\nSiguiente paso: subí fotos reales desde /admin-.../taller/itemcatalogo/'
        ))
