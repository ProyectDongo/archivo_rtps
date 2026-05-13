"""
Reclasifica correos ya importados que están como `tipo_carpeta='otros'`
usando una heurística simple sobre el remitente:

  - Si remitente contiene el email del buzón → 'enviados' (lo mandó el dueño)
  - Si tiene remitente diferente             → 'inbox'    (alguien le escribió)
  - Si remitente está vacío                  → 'otros'    (queda igual)

Pensado para los 2146 correos del primer import de archivos_viejos.mbox que
no tenían el campo cuando se importaron. Para los próximos imports (INBOX,
Sent, etc.) usá `import_mbox --tipo-carpeta=...` o dejá que la heurística
del nombre de archivo lo detecte.

Uso:
    python manage.py clasificar_correos                  # solo los 'otros'
    python manage.py clasificar_correos --buzon EMAIL    # un buzón específico
    python manage.py clasificar_correos --todos          # reclasifica TODO (ojo)
    python manage.py clasificar_correos --dry-run        # simula sin guardar
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from correos.models import Buzon, Correo


class Command(BaseCommand):
    help = 'Reclasifica correos por tipo_carpeta usando heurística sobre el remitente.'

    def add_arguments(self, parser):
        parser.add_argument('--buzon', type=str, help='Procesar solo este email de buzón.')
        parser.add_argument('--todos', action='store_true',
                            help='Reclasifica TODOS los correos, no solo los "otros". Sobreescribe clasificaciones previas.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Muestra qué cambiaría sin tocar la BD.')

    def handle(self, *args, **options):
        buzon_email = options.get('buzon')
        todos       = options['todos']
        dry         = options['dry_run']

        if buzon_email:
            buzones = Buzon.objects.filter(email=buzon_email.lower().strip())
            if not buzones.exists():
                self.stderr.write(f'No existe buzón con email {buzon_email}')
                return
        else:
            buzones = Buzon.objects.all()

        total_inbox     = 0
        total_enviados  = 0
        total_otros     = 0
        total_intactos  = 0

        for buzon in buzones:
            qs = buzon.correos.all()
            if not todos:
                qs = qs.filter(tipo_carpeta=Correo.Carpeta.OTROS)

            self.stdout.write(f'\n— Buzón: {buzon.email} ({qs.count()} correos a evaluar) —')

            # Enviados: el dueño del buzón aparece en el remitente.
            mask_enviados = qs.filter(remitente__icontains=buzon.email)
            n_env = mask_enviados.count()

            # Inbox: tiene remitente pero NO es el dueño.
            mask_inbox = qs.exclude(remitente__icontains=buzon.email).exclude(remitente='')
            n_in = mask_inbox.count()

            # Otros: queda igual (sin remitente o no clasificable).
            n_ot = qs.filter(remitente='').count()

            if dry:
                self.stdout.write(f'  [DRY] enviados: +{n_env}   inbox: +{n_in}   sin remitente: {n_ot}')
            else:
                with transaction.atomic():
                    actualizados_env = mask_enviados.update(tipo_carpeta=Correo.Carpeta.ENVIADOS)
                    actualizados_in  = mask_inbox.update(tipo_carpeta=Correo.Carpeta.INBOX)
                self.stdout.write(f'  enviados: {actualizados_env}   inbox: {actualizados_in}   sin clasificar: {n_ot}')

            total_enviados += n_env
            total_inbox    += n_in
            total_otros    += n_ot
            total_intactos += qs.count() - n_env - n_in - n_ot

        prefijo = '[DRY-RUN] ' if dry else ''
        self.stdout.write(self.style.SUCCESS(
            f'\n{prefijo}Resumen:\n'
            f'  → enviados:        {total_enviados}\n'
            f'  → inbox:           {total_inbox}\n'
            f'  → otros (intactos): {total_otros}\n'
            f'  → otros sin tocar:  {total_intactos}\n'
        ))
