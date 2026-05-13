"""
Genera datos de prueba para visualizar el inbox sin importar .mbox reales.

Uso:
  python manage.py seed_demo                    # 80 correos a soporte.dongo@gmail.com
  python manage.py seed_demo --buzon=foo@bar    # buzón distinto
  python manage.py seed_demo --n=200            # cantidad
  python manage.py seed_demo --limpiar          # borra antes de sembrar
"""
import random
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from correos.models import Adjunto, Buzon, Correo


# PDF mínimo válido (visualizable en Chrome/Firefox)
PDF_DEMO = (
    b'%PDF-1.4\n'
    b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
    b'2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n'
    b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]'
    b'/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n'
    b'4 0 obj<</Length 62>>stream\n'
    b'BT /F1 22 Tf 80 720 Td (Cotizacion Pietramonte - DEMO) Tj ET\n'
    b'endstream endobj\n'
    b'5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n'
    b'xref\n0 6\n'
    b'0000000000 65535 f\n0000000010 00000 n\n0000000053 00000 n\n'
    b'0000000095 00000 n\n0000000186 00000 n\n0000000295 00000 n\n'
    b'trailer<</Size 6/Root 1 0 R>>\nstartxref\n362\n%%EOF\n'
)

ADJUNTOS_DEMO = [
    ('cotizacion-revision.pdf', 'application/pdf', PDF_DEMO),
    ('factura-12345.pdf',       'application/pdf', PDF_DEMO),
    ('orden-trabajo.pdf',       'application/pdf', PDF_DEMO),
]


REMITENTES = [
    'Andrea Ledezma <andrea@cliente1.cl>',
    'Mecánica Express <ventas@mecanica-express.cl>',
    'Repuestos Atacama <admin@repuestos-atacama.cl>',
    'Banco Estado <noreply@bancoestado.cl>',
    'SII Notificaciones <notificaciones@sii.cl>',
    'AFP Modelo <contacto@afpmodelo.cl>',
    'Carlos Pietrasanta <cpietrasanta@pietramonte.cl>',
    'Vania Pietrasanta <vpietrasanta@pietramonte.cl>',
    'Roberto Albornoz <ralbornoz@pietramonte.cl>',
    'Juan Pérez <juan.perez@gmail.com>',
    'María González <mgonzalez@empresa.cl>',
    'TransBank <noreply@transbank.cl>',
    'Cobranza Hola <cobranza@empresa-x.cl>',
    'Cliente Tornillo <jose.t@hotmail.com>',
    'Soporte Hetzner <support@hetzner.com>',
]

ASUNTOS = [
    'Cotización por revisión de motor',
    'Re: Estado del pedido de repuestos',
    'Factura electrónica N° 12345',
    'Aviso de cobro pendiente',
    'Confirmación de cita - Lunes 09:30',
    'Nueva orden de trabajo',
    'Garantía de neumáticos',
    'Reagendar atención',
    'Presupuesto aire acondicionado',
    'Consulta repuestos Toyota Hilux',
    'Cambio de aceite y filtros',
    'Diagnóstico computarizado',
    'Pago confirmado',
    'Re: Re: Disponibilidad de pieza',
    'Notificación de impuestos',
    'Recordatorio mantención',
]

CUERPOS = [
    'Estimado, agradezco su pronta respuesta. Quedamos atentos para coordinar la siguiente atención según indicó.\n\nSaludos cordiales.',
    'Hola,\n\nAdjunto los detalles solicitados. Confirmamos disponibilidad para el día jueves a las 10:00.\n\nGracias.',
    'Buenos días,\n\nLes escribo para confirmar la recepción del vehículo en el taller. La revisión completa estaría lista mañana al mediodía.\n\nUn saludo.',
    'Estimados,\n\nSe ha generado una factura por los servicios prestados. El monto total asciende a $145.000. Pueden cancelar por transferencia o efectivo.\n\nQuedo atento.',
    'Confirmamos la cita para el lunes 09:30. Por favor, recordar traer el certificado de revisión técnica vigente.\n\nSaludos.',
    'Gracias por la rápida atención. El cambio de pastillas quedó perfecto y el ruido desapareció.\n\nLos contactaré para la próxima mantención.',
    'Adjunto cotización por reemplazo de batería y revisión del sistema de carga. Validez 7 días.\n\nMecánica Express',
    'Su orden de trabajo N° 4501 ha sido finalizada. Puede pasar a retirar el vehículo desde las 14:00.',
]


class Command(BaseCommand):
    help = 'Siembra datos demo de Buzon + Correos para visualizar el inbox'

    def add_arguments(self, parser):
        parser.add_argument('--buzon', default='soporte.dongo@gmail.com')
        parser.add_argument('--n', type=int, default=80)
        parser.add_argument('--limpiar', action='store_true')

    def handle(self, *args, **opts):
        email = opts['buzon'].lower().strip()
        n = opts['n']

        buzon, created = Buzon.objects.get_or_create(
            email=email,
            defaults={'nombre': 'Buzón demo'},
        )
        if opts['limpiar']:
            borrados, _ = buzon.correos.all().delete()
            self.stdout.write(self.style.WARNING(f'Borrados {borrados} correos previos'))

        ahora = timezone.now()
        creados = 0
        for i in range(n):
            # Distribuye fechas: la mitad en últimos 30 días, resto hasta 6 meses atrás
            if random.random() < 0.5:
                dias_atras = random.uniform(0, 30)
            else:
                dias_atras = random.uniform(30, 183)
            fecha = ahora - timedelta(days=dias_atras, hours=random.uniform(0, 24))

            tiene_adj = random.random() < 0.25
            correo = Correo.objects.create(
                buzon=buzon,
                mensaje_id=f'<demo-{i}-{random.randint(1000, 9999)}@example>',
                remitente=random.choice(REMITENTES),
                destinatario=email,
                asunto=random.choice(ASUNTOS),
                fecha=fecha,
                cuerpo_texto=random.choice(CUERPOS),
                tiene_adjunto=tiene_adj,
            )
            creados += 1

            # Adjuntos demo (PDF mínimo válido)
            if tiene_adj:
                for _ in range(random.randint(1, 2)):
                    nombre, mime, data = random.choice(ADJUNTOS_DEMO)
                    adj = Adjunto(
                        correo=correo,
                        nombre_original=nombre,
                        mime_type=mime,
                        tamano_bytes=len(data),
                    )
                    adj.archivo.save(nombre, ContentFile(data), save=False)
                    adj.save()

        # Actualiza contador
        buzon.total_correos = buzon.correos.count()
        buzon.save(update_fields=['total_correos'])

        self.stdout.write(self.style.SUCCESS(
            f'OK: {creados} correos creados en {email} '
            f'({"nuevo buzón" if created else "buzón existente"})'
        ))
